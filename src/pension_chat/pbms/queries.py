"""The only SQL the chatbot sends PBMS: named, parameterised, read-only.

Nothing here is assembled from user input. Each statement is checked at import
by `check_statement`, and a test runs the same check, so a statement that
writes, names a column outside `ALLOWED_COLUMNS`, or reads a personal
identifier cannot ship.

Employees are found by first and last name among *active* members (spec §3.2);
the MEAB ID is the internal key a choice resolves to. Salary is read through
`T_Pen_Ded_Master`, never by filtering `T_Pen_Ded_Detail` on the person: that
column has no index, and one member's read scanned 46.7M rows (spec §6.1).
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Query:
    sql: str
    params: tuple[str, ...]
    # Seconds; None means the client's default (5 s).
    timeout_s: float | None = None


# How a year's salary is built from pay periods, and whether service is counted
# from them -- settled against PBMS's own annuity calculations (spec §6.2).
SALARY_EXPRESSIONS = {
    "gross": "SUM(d.Gross)",
    "rate": "MAX(d.Ann_Sal)",
    "capped": "SUM(d.Capped_Salary)",
    # PBMS's own pensionable salary where it records one (2019 on for active
    # members: 12 x its monthly BestSal, and deductions are 8.5% of it); else
    # gross pay, but only when the deductions are plausible for it -- recent
    # Gross is empty or fragmentary for active members. Otherwise no salary.
    "capped_then_gross": (
        "CASE WHEN SUM(d.Capped_Salary) > 0 THEN SUM(d.Capped_Salary) "
        "WHEN SUM(d.Gross) > 0 AND SUM(d.Ded) <= 0.2 * SUM(d.Gross) THEN SUM(d.Gross) END"
    ),
}
SALARY_RULE = "capped_then_gross"
SERVICE_FROM_PAYROLL = True

ALLOWED_COLUMNS: frozenset[str] = frozenset({
    "MEAB_Id", "Person_Info_Seq_No", "Employee_Seq_No",
    "First_Name", "Last_Name", "Birth_Date",
    "Hire_Date", "First_Payroll_Date", "Seperation_Date", "Employee_Status_Cd",
    "Tier_Type_Cd", "Reciprocal_Flg", "ER_Type_Cd", "Er_Type_Short_Desc",
    "Department_Seq_No", "Department_Short_Desc", "Status",
    "Pen_Ded_Master_Seq_No", "PPE", "Gross", "Ann_Sal", "Capped_Salary", "Ded",
})

_WRITE = re.compile(
    r"\b(insert|update|delete|merge|exec|execute|drop|alter|create|truncate|grant|revoke|into|openrowset|openquery)\b",
    re.IGNORECASE,
)
_PERSONAL = re.compile(
    r"\b(e?ssn|cc\w*|pbsaddress\w*|pbsemail|pbs\w*_phone|maiden_name|middle_name|memberid)\b", re.IGNORECASE
)
# Every column is written alias.Column; the aliases are one or two letters.
_QUALIFIED = re.compile(r"\b[a-z]{1,2}\.([A-Za-z_]+)\b")


def check_statement(sql: str) -> None:
    """Raise ValueError unless `sql` is one read-only, allow-listed SELECT."""
    body = sql.strip()
    if not re.match(r"(?is)^select\b", body):
        raise ValueError("not a SELECT")
    if ";" in body:
        raise ValueError("more than one statement")
    if found := _WRITE.search(body):
        raise ValueError(f"write keyword {found.group(0)!r}")
    if found := _PERSONAL.search(body):
        raise ValueError(f"personal identifier {found.group(0)!r}")
    outside = set(_QUALIFIED.findall(body)) - ALLOWED_COLUMNS
    if outside:
        raise ValueError(f"columns outside the allow-list: {sorted(outside)}")


_ACTIVE = "l.MEAB_Id LIKE 'M-%' AND e.Status = 'A' AND e.Employee_Status_Cd = 'A'"

_PEOPLE = """
  FROM dbo.T_Pbs_Linker l
  JOIN dbo.T_Person_Info p ON p.Person_Info_Seq_No = l.Person_Info_Seq_No
  JOIN dbo.T_Employees e ON e.Employee_Seq_No = l.Employee_Seq_No"""

_DESCRIBED = """
  LEFT JOIN dbo.T_Employer_Types et ON et.ER_Type_Cd = e.ER_Type_Cd
  LEFT JOIN dbo.T_Departments dp ON dp.Department_Seq_No = e.Department_Seq_No"""

_WHO = """
       l.MEAB_Id, p.First_Name, p.Last_Name, YEAR(p.Birth_Date) AS Birth_Year,
       e.Employee_Status_Cd, e.ER_Type_Cd, et.Er_Type_Short_Desc, dp.Department_Short_Desc"""

_PAY_JOIN = """
  FROM dbo.T_Pbs_Linker l
  JOIN dbo.T_Pen_Ded_Master m ON m.Person_Info_Seq_No = l.Person_Info_Seq_No
  JOIN dbo.T_Pen_Ded_Detail d ON d.Pen_Ded_Master_Seq_No = m.Pen_Ded_Master_Seq_No
 WHERE l.MEAB_Id = ? AND m.Status = 'A' AND d.Status = 'A'"""

REGISTRY: dict[str, Query] = {
    "name_directory": Query(
        f"""SELECT DISTINCT p.First_Name, p.Last_Name{_PEOPLE}
 WHERE {_ACTIVE} AND p.First_Name IS NOT NULL AND p.Last_Name IS NOT NULL""",
        (),
        timeout_s=30,
    ),
    "resolve_name": Query(
        f"""SELECT{_WHO}{_PEOPLE}{_DESCRIBED}
 WHERE {_ACTIVE} AND p.First_Name = ? AND p.Last_Name = ?""",
        ("first_name", "last_name"),
    ),
    "resolve_meab_id": Query(
        f"""SELECT{_WHO}{_PEOPLE}{_DESCRIBED}
 WHERE {_ACTIVE} AND l.MEAB_Id = ?""",
        ("meab_id",),
    ),
    "record": Query(
        f"""SELECT{_WHO}, p.Birth_Date,
       e.Hire_Date, e.First_Payroll_Date, e.Seperation_Date, e.Tier_Type_Cd, e.Reciprocal_Flg{_PEOPLE}{_DESCRIBED}
 WHERE l.MEAB_Id = ?""",
        ("meab_id",),
    ),
    "salary_years": Query(
        f"""SELECT YEAR(d.PPE) AS Year, {SALARY_EXPRESSIONS[SALARY_RULE]} AS Salary,
       COUNT(DISTINCT CASE WHEN d.Ded > 0 THEN MONTH(d.PPE) END) AS Contribution_Months{_PAY_JOIN}
 GROUP BY YEAR(d.PPE)
 ORDER BY YEAR(d.PPE)""",
        ("meab_id",),
    ),
}

for _name, _query in REGISTRY.items():
    check_statement(_query.sql)
