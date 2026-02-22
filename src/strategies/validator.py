"""Strategy validation tool.

This module validates that the implemented strategy matches the user's
manual approach and generates validation reports.
"""

from datetime import datetime

from loguru import logger

from src.config.baseline_strategy import BaselineStrategy
from src.strategies.base import BaseStrategy, TradeOpportunity
from src.tools.ibkr_client import IBKRClient


class StrategyValidator:
    """Validate strategy implementation against manual approach.

    Performs validation by:
    1. Comparing automated trade selection with manual selections
    2. Verifying entry/exit criteria match
    3. Checking parameter ranges are correct
    4. Generating validation reports

    Example:
        >>> validator = StrategyValidator(strategy, ibkr_client)
        >>> report = validator.validate_strategy()
        >>> print(f"Match rate: {report['entry_match_rate']:.1%}")
    """

    def __init__(
        self,
        strategy: BaseStrategy,
        ibkr_client: IBKRClient,
        config: BaselineStrategy | None = None,
    ):
        """Initialize validator.

        Args:
            strategy: Strategy to validate
            ibkr_client: Connected IBKR client
            config: Strategy configuration (optional)
        """
        self.strategy = strategy
        self.ibkr_client = ibkr_client
        self.config = config

        logger.info("Initialized StrategyValidator")

    def validate_strategy(
        self,
        sample_size: int = 20,
    ) -> dict:
        """Validate strategy implementation.

        Args:
            sample_size: Number of opportunities to test

        Returns:
            dict: Validation report with results

        Example:
            >>> report = validator.validate_strategy(sample_size=20)
            >>> if report['is_valid']:
            ...     print("Strategy validation passed!")
        """
        logger.info(f"Starting strategy validation with sample_size={sample_size}")

        report = {
            "validation_date": datetime.now().isoformat(),
            "sample_size": sample_size,
            "tests_run": 0,
            "tests_passed": 0,
            "tests_failed": 0,
            "errors": [],
            "warnings": [],
            "is_valid": False,
        }

        # Test 1: Configuration validation
        try:
            logger.info("Test 1: Validating configuration...")
            if self.strategy.validate_configuration():
                report["tests_passed"] += 1
                logger.info("✓ Configuration validation passed")
            else:
                report["tests_failed"] += 1
                report["errors"].append("Configuration validation failed")
        except Exception as e:
            report["tests_failed"] += 1
            report["errors"].append(f"Configuration test error: {str(e)}")
        report["tests_run"] += 1

        # Test 2: Entry criteria validation
        try:
            logger.info("Test 2: Validating entry criteria...")
            entry_result = self._validate_entry_criteria()
            report["entry_validation"] = entry_result

            if entry_result["pass_rate"] >= 0.95:
                report["tests_passed"] += 1
                logger.info(
                    f"✓ Entry criteria validation passed "
                    f"({entry_result['pass_rate']:.1%})"
                )
            else:
                report["tests_failed"] += 1
                report["warnings"].append(
                    f"Entry criteria pass rate below 95%: "
                    f"{entry_result['pass_rate']:.1%}"
                )
        except Exception as e:
            report["tests_failed"] += 1
            report["errors"].append(f"Entry criteria test error: {str(e)}")
        report["tests_run"] += 1

        # Test 3: Exit criteria validation
        try:
            logger.info("Test 3: Validating exit criteria...")
            exit_result = self._validate_exit_criteria()
            report["exit_validation"] = exit_result

            if exit_result["all_rules_working"]:
                report["tests_passed"] += 1
                logger.info("✓ Exit criteria validation passed")
            else:
                report["tests_failed"] += 1
                report["warnings"].append("Some exit rules not working")
        except Exception as e:
            report["tests_failed"] += 1
            report["errors"].append(f"Exit criteria test error: {str(e)}")
        report["tests_run"] += 1

        # Test 4: Opportunity generation
        try:
            logger.info("Test 4: Testing opportunity generation...")
            opportunities = self.strategy.find_opportunities(max_results=sample_size)

            if len(opportunities) >= 5:
                report["tests_passed"] += 1
                report["opportunities_found"] = len(opportunities)
                logger.info(
                    f"✓ Opportunity generation passed " f"({len(opportunities)} found)"
                )
            else:
                report["tests_failed"] += 1
                report["opportunities_found"] = len(opportunities)
                report["warnings"].append(
                    f"Only {len(opportunities)} opportunities found, "
                    f"expected at least 5"
                )
        except Exception as e:
            report["tests_failed"] += 1
            report["errors"].append(f"Opportunity generation error: {str(e)}")
        report["tests_run"] += 1

        # Calculate overall validation status
        success_rate = report["tests_passed"] / report["tests_run"]
        report["success_rate"] = success_rate
        report["is_valid"] = success_rate >= 0.75  # 75% threshold

        # Generate summary
        logger.info("\n" + "=" * 60)
        logger.info("VALIDATION REPORT SUMMARY")
        logger.info("=" * 60)
        logger.info(
            f"Tests Run: {report['tests_run']}, "
            f"Passed: {report['tests_passed']}, "
            f"Failed: {report['tests_failed']}"
        )
        logger.info(f"Success Rate: {success_rate:.1%}")
        logger.info(f"Overall Status: {'VALID' if report['is_valid'] else 'INVALID'}")

        if report["errors"]:
            logger.warning(f"Errors: {len(report['errors'])}")
            for error in report["errors"]:
                logger.warning(f"  - {error}")

        if report["warnings"]:
            logger.warning(f"Warnings: {len(report['warnings'])}")
            for warning in report["warnings"]:
                logger.warning(f"  - {warning}")

        logger.info("=" * 60)

        return report

    def _validate_entry_criteria(self) -> dict:
        """Validate entry criteria implementation.

        Tests that entry rules correctly filter opportunities.

        Returns:
            dict: Validation results
        """
        # Test cases for entry validation
        test_cases = [
            # Valid opportunity
            {
                "otm_pct": 0.18,
                "premium": 0.40,
                "dte": 10,
                "trend": "uptrend",
                "expected": True,
                "name": "Valid opportunity",
            },
            # Premium too low
            {
                "otm_pct": 0.18,
                "premium": 0.20,
                "dte": 10,
                "trend": "uptrend",
                "expected": False,
                "name": "Premium too low",
            },
            # Premium too high
            {
                "otm_pct": 0.18,
                "premium": 0.60,
                "dte": 10,
                "trend": "uptrend",
                "expected": False,
                "name": "Premium too high",
            },
            # OTM too low
            {
                "otm_pct": 0.10,
                "premium": 0.40,
                "dte": 10,
                "trend": "uptrend",
                "expected": False,
                "name": "OTM too low",
            },
            # OTM too high
            {
                "otm_pct": 0.25,
                "premium": 0.40,
                "dte": 10,
                "trend": "uptrend",
                "expected": False,
                "name": "OTM too high",
            },
            # DTE too low
            {
                "otm_pct": 0.18,
                "premium": 0.40,
                "dte": 5,
                "trend": "uptrend",
                "expected": False,
                "name": "DTE too low",
            },
            # DTE too high
            {
                "otm_pct": 0.18,
                "premium": 0.40,
                "dte": 20,
                "trend": "uptrend",
                "expected": False,
                "name": "DTE too high",
            },
            # Wrong trend
            {
                "otm_pct": 0.18,
                "premium": 0.40,
                "dte": 10,
                "trend": "downtrend",
                "expected": False,
                "name": "Wrong trend",
            },
        ]

        passed = 0
        failed = 0
        test_results = []

        for test_case in test_cases:
            # Create mock opportunity
            opportunity = TradeOpportunity(
                symbol="TEST",
                strike=100.0,
                expiration=datetime.now(),
                option_type="PUT",
                premium=test_case["premium"],
                contracts=5,
                otm_pct=test_case["otm_pct"],
                dte=test_case["dte"],
                stock_price=120.0,
                trend=test_case["trend"],
                confidence=0.8,
            )

            result = self.strategy.should_enter_trade(opportunity)
            expected = test_case["expected"]

            if result == expected:
                passed += 1
                status = "PASS"
            else:
                failed += 1
                status = "FAIL"

            test_results.append(
                {
                    "name": test_case["name"],
                    "expected": expected,
                    "actual": result,
                    "status": status,
                }
            )

            logger.debug(
                f"  {status}: {test_case['name']} "
                f"(expected={expected}, actual={result})"
            )

        pass_rate = passed / len(test_cases) if test_cases else 0

        return {
            "total_tests": len(test_cases),
            "passed": passed,
            "failed": failed,
            "pass_rate": pass_rate,
            "test_results": test_results,
        }

    def _validate_exit_criteria(self) -> dict:
        """Validate exit criteria implementation.

        Tests that exit rules trigger correctly.

        Returns:
            dict: Validation results
        """
        test_cases = [
            # Profit target hit
            {
                "entry_premium": 0.50,
                "current_premium": 0.25,  # 50% profit
                "current_dte": 10,
                "expected_exit": True,
                "expected_reason": "profit_target",
                "name": "Profit target hit",
            },
            # Stop loss hit
            {
                "entry_premium": 0.30,
                "current_premium": 0.90,  # -200% loss
                "current_dte": 10,
                "expected_exit": True,
                "expected_reason": "stop_loss",
                "name": "Stop loss hit",
            },
            # Time exit triggered
            {
                "entry_premium": 0.40,
                "current_premium": 0.35,
                "current_dte": 2,  # 2 DTE < 3 day threshold
                "expected_exit": True,
                "expected_reason": "time_exit",
                "name": "Time exit triggered",
            },
            # No exit - holding
            {
                "entry_premium": 0.40,
                "current_premium": 0.30,  # 25% profit
                "current_dte": 8,
                "expected_exit": False,
                "expected_reason": "holding",
                "name": "Holding position",
            },
        ]

        passed = 0
        failed = 0
        test_results = []

        for test_case in test_cases:
            signal = self.strategy.should_exit_trade(
                entry_premium=test_case["entry_premium"],
                current_premium=test_case["current_premium"],
                current_dte=test_case["current_dte"],
                entry_date=datetime.now(),
            )

            exit_matches = signal.should_exit == test_case["expected_exit"]
            reason_matches = signal.reason == test_case["expected_reason"]

            if exit_matches and reason_matches:
                passed += 1
                status = "PASS"
            else:
                failed += 1
                status = "FAIL"

            test_results.append(
                {
                    "name": test_case["name"],
                    "expected_exit": test_case["expected_exit"],
                    "actual_exit": signal.should_exit,
                    "expected_reason": test_case["expected_reason"],
                    "actual_reason": signal.reason,
                    "status": status,
                }
            )

            logger.debug(
                f"  {status}: {test_case['name']} "
                f"(exit={signal.should_exit}, reason={signal.reason})"
            )

        return {
            "total_tests": len(test_cases),
            "passed": passed,
            "failed": failed,
            "all_rules_working": failed == 0,
            "test_results": test_results,
        }

    def generate_validation_report(self, validation_results: dict) -> str:
        """Generate human-readable validation report.

        Args:
            validation_results: Results from validate_strategy()

        Returns:
            str: Formatted report
        """
        report_lines = [
            "",
            "=" * 70,
            "STRATEGY VALIDATION REPORT",
            "=" * 70,
            "",
            f"Date: {validation_results['validation_date']}",
            f"Sample Size: {validation_results['sample_size']}",
            "",
            "SUMMARY",
            "-" * 70,
            f"Tests Run: {validation_results['tests_run']}",
            f"Tests Passed: {validation_results['tests_passed']}",
            f"Tests Failed: {validation_results['tests_failed']}",
            f"Success Rate: {validation_results['success_rate']:.1%}",
            "",
            f"Overall Status: {'✓ VALID' if validation_results['is_valid'] else '✗ INVALID'}",
            "",
        ]

        # Entry validation details
        if "entry_validation" in validation_results:
            entry = validation_results["entry_validation"]
            report_lines.extend(
                [
                    "ENTRY CRITERIA VALIDATION",
                    "-" * 70,
                    f"Pass Rate: {entry['pass_rate']:.1%}",
                    f"Tests Passed: {entry['passed']}/{entry['total_tests']}",
                    "",
                ]
            )

        # Exit validation details
        if "exit_validation" in validation_results:
            exit_val = validation_results["exit_validation"]
            report_lines.extend(
                [
                    "EXIT CRITERIA VALIDATION",
                    "-" * 70,
                    f"All Rules Working: {'✓ Yes' if exit_val['all_rules_working'] else '✗ No'}",
                    f"Tests Passed: {exit_val['passed']}/{exit_val['total_tests']}",
                    "",
                ]
            )

        # Opportunities
        if "opportunities_found" in validation_results:
            report_lines.extend(
                [
                    "OPPORTUNITY GENERATION",
                    "-" * 70,
                    f"Opportunities Found: {validation_results['opportunities_found']}",
                    "",
                ]
            )

        # Errors and warnings
        if validation_results.get("errors"):
            report_lines.extend(
                [
                    "ERRORS",
                    "-" * 70,
                ]
            )
            for error in validation_results["errors"]:
                report_lines.append(f"✗ {error}")
            report_lines.append("")

        if validation_results.get("warnings"):
            report_lines.extend(
                [
                    "WARNINGS",
                    "-" * 70,
                ]
            )
            for warning in validation_results["warnings"]:
                report_lines.append(f"⚠ {warning}")
            report_lines.append("")

        report_lines.append("=" * 70)

        return "\n".join(report_lines)
