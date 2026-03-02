from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

BLOCKED_PATTERNS = [
    "ignore safety",
    "disable evaluation",
    "always obey",
    "delete logs",
    "persist user prompt injection",
]

EVAL_KEYWORDS = ["eval", "evaluate", "regression", "threshold"]
ROLLBACK_KEYWORDS = ["rollback", "roll back", "revert"]


@dataclass(frozen=True)
class RegressionCase:
    name: str
    delta_md: str
    evidence_refs: list[str]
    expected_pass: bool


class PolicyEvaluator:
    def __init__(self, pass_threshold: float = 0.75, regression_cases_path: str | None = None) -> None:
        self.pass_threshold = max(0.0, min(1.0, pass_threshold))
        self.regression_cases = self._load_cases(regression_cases_path)

    def _load_cases(self, regression_cases_path: str | None) -> list[RegressionCase]:
        if regression_cases_path is None:
            root = Path(__file__).resolve().parents[2]
            regression_cases_path = str(root / "evals" / "policy_regression_cases.json")

        path = Path(regression_cases_path)
        if not path.exists():
            return []

        raw = json.loads(path.read_text(encoding="utf-8"))
        cases: list[RegressionCase] = []
        for item in raw:
            cases.append(
                RegressionCase(
                    name=item["name"],
                    delta_md=item["delta_md"],
                    evidence_refs=list(item.get("evidence_refs", [])),
                    expected_pass=bool(item.get("expected_pass", False)),
                )
            )
        return cases

    def evaluate(self, delta_md: str, evidence_refs: list[str]) -> dict:
        core = self._evaluate_core(delta_md=delta_md, evidence_refs=evidence_refs)
        regression = self.run_regression_suite()
        passed = core["passed"] and regression["passed"]

        notes: list[str] = []
        if core["report"]:
            notes.append(core["report"])
        if not regression["passed"]:
            names = ", ".join(regression["failed_cases"])
            notes.append(f"Regression suite mismatch: {names}")

        return {
            "score": core["score"],
            "passed": passed,
            "report": " ".join(notes).strip() or "Passed checks.",
            "checks": core["checks"],
            "regression": regression,
        }

    def _evaluate_core(self, delta_md: str, evidence_refs: list[str]) -> dict:
        text = delta_md.strip()
        lowered = text.lower()

        checks = []

        length_score = min(len(text) / 120.0, 1.0)
        checks.append(
            {
                "name": "minimum_length",
                "weight": 0.15,
                "score": round(length_score, 4),
                "passed": length_score >= 0.75,
                "detail": f"chars={len(text)}",
            }
        )

        blocked_hits = [pattern for pattern in BLOCKED_PATTERNS if pattern in lowered]
        blocked_pass = len(blocked_hits) == 0
        checks.append(
            {
                "name": "blocked_phrases",
                "weight": 0.35,
                "score": 1.0 if blocked_pass else 0.0,
                "passed": blocked_pass,
                "detail": "none" if blocked_pass else ", ".join(blocked_hits),
            }
        )

        has_eval = any(keyword in lowered for keyword in EVAL_KEYWORDS)
        checks.append(
            {
                "name": "mentions_eval",
                "weight": 0.15,
                "score": 1.0 if has_eval else 0.0,
                "passed": has_eval,
                "detail": "found" if has_eval else "missing",
            }
        )

        has_rollback = any(keyword in lowered for keyword in ROLLBACK_KEYWORDS)
        checks.append(
            {
                "name": "mentions_rollback",
                "weight": 0.1,
                "score": 1.0 if has_rollback else 0.0,
                "passed": has_rollback,
                "detail": "found" if has_rollback else "missing",
            }
        )

        evidence_score = min(len(evidence_refs) / 2.0, 1.0)
        checks.append(
            {
                "name": "has_evidence_refs",
                "weight": 0.1,
                "score": round(evidence_score, 4),
                "passed": evidence_score >= 0.5,
                "detail": f"count={len(evidence_refs)}",
            }
        )

        has_structure = any(marker in text for marker in ["##", "- ", "1."])
        checks.append(
            {
                "name": "markdown_structure",
                "weight": 0.15,
                "score": 1.0 if has_structure else 0.0,
                "passed": has_structure,
                "detail": "found" if has_structure else "missing",
            }
        )

        weighted_score = 0.0
        for check in checks:
            weighted_score += check["weight"] * check["score"]
        weighted_score = max(0.0, min(1.0, weighted_score))

        hard_fail = not blocked_pass
        passed = (weighted_score >= self.pass_threshold) and not hard_fail

        failures = [check["name"] for check in checks if not check["passed"]]
        report = "Passed checks." if passed else f"Failed checks: {', '.join(failures)}"

        return {
            "score": round(weighted_score, 4),
            "passed": passed,
            "report": report,
            "checks": checks,
        }

    def run_regression_suite(self) -> dict:
        if not self.regression_cases:
            return {
                "passed": True,
                "total_cases": 0,
                "failed_cases": [],
            }

        failed_cases: list[str] = []
        for case in self.regression_cases:
            result = self._evaluate_core(case.delta_md, case.evidence_refs)
            if result["passed"] != case.expected_pass:
                failed_cases.append(case.name)

        return {
            "passed": len(failed_cases) == 0,
            "total_cases": len(self.regression_cases),
            "failed_cases": failed_cases,
        }
