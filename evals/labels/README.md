# Judge calibration labels

Label about 20 turns from a saved run (`evals/runs/<ts>/traces/*.json`). Each file is a YAML list:

    - id: "S1-003#0"          # case id # turn index
      goal: yes               # yes | partly | no
      relevant: true
      within_boundary: true

Then run `uv run python scripts/run_evals.py --calibrate --from evals/runs/<ts>`.
The judge gates pass/fail only when it agrees on at least 18 of 20 for the current rubric version.
