"""Phase 1 discovery against PBMS: which salary PBMS uses, how it holds service, how fast reads are.

Read-only and aggregate-only. It prints counts, rates, codes, medians and
timings -- never a name, an ID, a date of birth or an amount belonging to one
person. Personal values live in memory only while they are compared.

    uv run python scripts/pbms_discover.py codes
    uv run python scripts/pbms_discover.py links
    uv run python scripts/pbms_discover.py salary --link-table <table from `links`>
    uv run python scripts/pbms_discover.py service --link-table <table from `links`>
    uv run python scripts/pbms_discover.py timing        # after Task 4
"""
from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

SAMPLE = 60
_MONEY = ("gross", "rate", "capped")


def _connect():
    import pyodbc

    from pension_chat.config import get_settings

    s = get_settings()
    if not (s.pbms_sql_server and s.pbms_sql_database and s.pbms_sql_user and s.pbms_sql_password):
        raise SystemExit("PBMS_SQL_* are not set in .env")

    def braced(v: str) -> str:
        return "{" + v.replace("}", "}}") + "}"

    conn = pyodbc.connect(
        "DRIVER={ODBC Driver 18 for SQL Server};"
        f"SERVER={s.pbms_sql_server};DATABASE={braced(s.pbms_sql_database)};"
        f"UID={braced(s.pbms_sql_user)};PWD={braced(s.pbms_sql_password.get_secret_value())};"
        "Encrypt=yes;TrustServerCertificate=yes;ApplicationIntent=ReadOnly;APP=pension-chat-discovery;",
        timeout=20, autocommit=True, readonly=True,
    )
    conn.timeout = 120
    conn.execute("SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED")
    return conn


def _rows(conn, sql: str, *args):
    cur = conn.cursor()
    cur.execute(sql, args)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def codes(conn) -> None:
    print("== Employee_Status_Cd on live rows (code, count)")
    for r in _rows(conn, "SELECT e.Employee_Status_Cd AS code, COUNT(*) AS n FROM dbo.T_Employees e "
                         "WHERE e.Status = 'A' GROUP BY e.Employee_Status_Cd ORDER BY 2 DESC"):
        print(f"  {r['code']!r}: {r['n']}")
    print("== tables that describe Employee_Status_Cd")
    for r in _rows(conn, "SELECT OBJECT_NAME(c.object_id) AS t FROM sys.columns c JOIN sys.tables t "
                         "ON t.object_id = c.object_id WHERE c.name = 'Employee_Status_Cd' "
                         "AND OBJECT_NAME(c.object_id) LIKE '%Type%'"):
        print(f"  {r['t']}")
    print("== Status codes on T_Pen_Ded_Master for 2024, and on a server-side sample of its detail rows")
    for r in _rows(conn, "SELECT m.Status AS code, COUNT(*) AS n FROM dbo.T_Pen_Ded_Master m "
                         "WHERE m.Year = 2024 GROUP BY m.Status"):
        print(f"  master {r['code']!r}: {r['n']}")
    for r in _rows(conn, "SELECT x.code, COUNT(*) AS n FROM (SELECT TOP 200000 d.Status AS code "
                         "FROM dbo.T_Pen_Ded_Master m JOIN dbo.T_Pen_Ded_Detail d "
                         "ON d.Pen_Ded_Master_Seq_No = m.Pen_Ded_Master_Seq_No WHERE m.Year = 2024) x "
                         "GROUP BY x.code"):
        print(f"  detail {r['code']!r}: {r['n']}")


def links(conn) -> None:
    print("== tables holding both Annuity_Seq_No and Person_Info_Seq_No (candidates for --link-table)")
    for r in _rows(conn, "SELECT OBJECT_NAME(a.object_id) AS t FROM sys.columns a JOIN sys.columns b "
                         "ON b.object_id = a.object_id AND b.name = 'Person_Info_Seq_No' "
                         "JOIN sys.tables t ON t.object_id = a.object_id WHERE a.name = 'Annuity_Seq_No'"):
        print(f"  {r['t']}")


def _sample(conn, link: str):
    """Retirees PBMS has worked an annuity for: (person, PBMS annual FAS, PBMS service)."""
    return _rows(conn, f"SELECT TOP {SAMPLE} x.Person_Info_Seq_No AS person, s.FAS_Monthly * 12 AS fas, "
                       f"s.MEAB_Svc AS svc FROM dbo.[{link}] x JOIN dbo.T_Annuity_Service_Salary s "
                       "ON s.Annuity_Seq_No = x.Annuity_Seq_No WHERE s.FAS_Monthly > 0 "
                       "ORDER BY x.Annuity_Seq_No DESC")


def _years(conn, person: int, status_filter: bool):
    where = "AND m.Status = 'A' AND d.Status = 'A'" if status_filter else ""
    return _rows(conn, "SELECT YEAR(d.PPE) AS y, SUM(d.Gross) AS gross, MAX(d.Ann_Sal) AS rate, "
                       "SUM(d.Capped_Salary) AS capped, "
                       "COUNT(DISTINCT CASE WHEN d.Ded > 0 THEN MONTH(d.PPE) END) AS months "
                       "FROM dbo.T_Pen_Ded_Master m JOIN dbo.T_Pen_Ded_Detail d "
                       "ON d.Pen_Ded_Master_Seq_No = m.Pen_Ded_Master_Seq_No "
                       f"WHERE m.Person_Info_Seq_No = ? {where} GROUP BY YEAR(d.PPE)", person)


def _best_four(by_year: dict[int, float]) -> float | None:
    """Highest average of 4 consecutive years within the last 10 years on file."""
    if not by_year:
        return None
    last = max(by_year)
    best = None
    for start in range(last - 9, last - 2):
        run = [by_year.get(y) for y in range(start, start + 4)]
        if all(v is not None for v in run):
            avg = sum(run) / 4
            best = avg if best is None else max(best, avg)
    return best


def salary(conn, link: str) -> None:
    people = _sample(conn, link)
    print(f"== {len(people)} retirees; share whose best-4-year average is within 1% of PBMS's FAS")
    for status_filter in (True, False):
        hits = {k: 0 for k in _MONEY}
        for p in people:
            years = _years(conn, p["person"], status_filter)
            for k in _MONEY:
                fas = _best_four({r["y"]: float(r[k] or 0) for r in years if r[k]})
                if fas and abs(fas - float(p["fas"])) <= 0.01 * float(p["fas"]):
                    hits[k] += 1
        label = "Status='A' rows only" if status_filter else "all rows"
        print(f"  {label}: " + ", ".join(f"{k}={hits[k]}/{len(people)}" for k in _MONEY))


def service(conn, link: str) -> None:
    people = _sample(conn, link)
    pbms, counted, close = [], [], 0
    for p in people:
        if p["svc"] is None:
            continue
        months = sum(int(r["months"] or 0) for r in _years(conn, p["person"], True))
        pbms.append(float(p["svc"]))
        counted.append(months / 12)
        close += abs(float(p["svc"]) - months / 12) <= 0.5
    print(f"== {len(pbms)} retirees with a PBMS service figure")
    if pbms:
        print(f"  median PBMS MEAB_Svc: {statistics.median(pbms):.2f}")
        print(f"  median counted-from-payroll years: {statistics.median(counted):.2f}")
        print(f"  within half a year: {close}/{len(pbms)}")


def peryear(conn) -> None:
    """Our per-year candidates against PBMS's own per-year basis (T_Annuity_Calc_SerSalByYear)."""
    calcs = _rows(conn, f"SELECT TOP {SAMPLE} c.Ann_Calc_Seq_No AS calc, c.Person_Info_Seq_No AS person "
                        "FROM dbo.T_Annuity_Calc c WHERE c.Person_Info_Seq_No IS NOT NULL "
                        "AND EXISTS (SELECT 1 FROM dbo.T_Annuity_Calc_SerSalByYear y "
                        "WHERE y.Ann_Calc_Seq_No = c.Ann_Calc_Seq_No) ORDER BY c.Ann_Calc_Seq_No DESC")
    salary_hits = {f"{k}~{p}": 0 for k in _MONEY for p in ("HighSal", "BestSal", "PredomSal")}
    ratios: dict[str, list[float]] = {k: [] for k in salary_hits}
    service_hits = {f"months~{p}": 0 for p in ("RegServ", "PaidServ", "MthSvc", "MonthCount/12")}
    years = 0
    for c in calcs:
        theirs = {r["Year"]: r for r in _rows(
            conn, "SELECT y.Year, y.HighSal, y.BestSal, y.PredomSal, y.RegServ, y.PaidServ, y.MthSvc, y.MonthCount "
                  "FROM dbo.T_Annuity_Calc_SerSalByYear y WHERE y.Ann_Calc_Seq_No = ?", c["calc"])}
        ours = {r["y"]: r for r in _years(conn, c["person"], True)}
        for year, t in theirs.items():
            o = ours.get(year)
            if not o:
                continue
            years += 1
            for k in _MONEY:
                for p in ("HighSal", "BestSal", "PredomSal"):
                    if o[k] and t[p]:
                        ratios[f"{k}~{p}"].append(float(o[k]) / float(t[p]))
                    if o[k] and t[p] and abs(float(o[k]) - float(t[p])) <= 0.01 * float(t[p]):
                        salary_hits[f"{k}~{p}"] += 1
            counted = int(o["months"] or 0) / 12
            for p in ("RegServ", "PaidServ", "MthSvc"):
                if t[p] is not None and abs(counted - float(t[p])) <= 0.05:
                    service_hits[f"months~{p}"] += 1
            if t["MonthCount"] is not None and abs(counted - int(t["MonthCount"]) / 12) <= 0.05:
                service_hits["months~MonthCount/12"] += 1
    print(f"== {len(calcs)} annuity calcs, {years} person-years present on both sides; share within 1% / 0.05 yr")
    print("  salary: " + ", ".join(f"{k}={v}/{years}" for k, v in salary_hits.items()))
    print("  service: " + ", ".join(f"{k}={v}/{years}" for k, v in service_hits.items()))
    print("  median ours/theirs: " + ", ".join(
        f"{k}={statistics.median(v):.3f} (n={len(v)})" for k, v in ratios.items() if v))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("what", choices=["codes", "links", "salary", "service", "peryear"])
    ap.add_argument("--link-table")
    args = ap.parse_args()
    if args.what in ("salary", "service") and not args.link_table:
        raise SystemExit("--link-table is required; run `links` first")
    conn = _connect()
    try:
        {"codes": lambda: codes(conn), "links": lambda: links(conn),
         "salary": lambda: salary(conn, args.link_table),
         "service": lambda: service(conn, args.link_table), "peryear": lambda: peryear(conn)}[args.what]()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
