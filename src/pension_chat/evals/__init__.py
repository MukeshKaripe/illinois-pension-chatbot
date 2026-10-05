"""The eval suite (docs/superpowers/specs/2026-09-30-eval-suite-design.md).

Three ideas carried over from the member-first harness it replaced:
a case is a conversation, not a question -- the failures that matter are a
correction that did not propagate or a provision forgotten by turn five; a
critical case fails the run whatever the overall score says; and a case for a
capability not yet built is run and reported, not scored.
"""
