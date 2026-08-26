"""
CLI Script to run Controlled Batch Recovery Evaluation

Usage:
  python scripts/run_batch_evaluation.py --cases 100 --seed 42 --mode all
  python scripts/run_batch_evaluation.py --cases 100 --seed 42 --mode llm --json-output evaluation_results.json
"""

import argparse
import json
import os
import sys

# Ensure UTF-8 stdout on Windows
sys.stdout.reconfigure(encoding="utf-8")

# Add parent directory to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.evaluation_engine import run_batch_evaluation


def print_evaluation_summary(result: dict, title_prefix: str = ""):
    metrics = result["metrics"]
    breakdown = result["category_breakdown"]

    print("\n" + "=" * 82)
    print(f"  {title_prefix}BATCH RECOVERY EVALUATION (SIMULATION / BENCHMARK)")
    print("=" * 82)
    print(f"  Run ID                  : {metrics['run_id']}")
    print(f"  Evaluation Mode         : {metrics['mode'].upper()}")
    print(f"  Dataset Size (Cases)    : {metrics['total_cases']}")
    print(f"  Random Seed             : {metrics['seed']}")
    print(f"  Total Revenue at Risk   : ₹{metrics['total_revenue_at_risk']:,.2f}")
    print(f"  Recovered Revenue       : ₹{metrics['recovered_revenue']:,.2f}")
    print(f"  Unrecovered Revenue     : ₹{metrics['unrecovered_revenue']:,.2f}")
    print(f"  Recovery Rate           : {metrics['recovery_rate_percentage']:.1f}%")
    print(f"  Auto-Recovered (<₹50k)  : ₹{metrics['auto_recovered_revenue']:,.2f}")
    print(f"  Human-Approved Revenue  : ₹{metrics['human_approved_revenue']:,.2f}")
    print(f"  Blocked Fraud Revenue   : ₹{metrics['blocked_fraud_revenue']:,.2f}")
    print("-" * 82)
    print("  AI DECISION & POLICY METRICS:")
    print(f"  LLM Decisions           : {metrics['llm_decisions']} ({metrics['llm_decision_rate_percentage']:.1f}%)")
    print(f"  Fallback Decisions      : {metrics['fallback_decisions']} ({metrics['fallback_rate_percentage']:.1f}%)")
    print(f"  Human Approvals Req.    : {metrics['human_approvals']} ({metrics['human_approval_rate_percentage']:.1f}%)")
    print(f"  Fraud / Security Blocks : {metrics['fraud_blocks']} ({metrics['fraud_block_rate_percentage']:.1f}%)")
    print(f"  Avg Failed Amount       : ₹{metrics['average_failed_amount']:,.2f}")
    print(f"  Avg Recovered Amount    : ₹{metrics['average_recovered_amount']:,.2f}")
    print(f"  Avg Recovery Latency    : {metrics['average_time_to_recovery_hours']:.1f} hours")
    print("-" * 82)
    print("  BALANCED 9-CATEGORY BREAKDOWN:")
    print(f"  {'Category':<28} | {'Cases':<5} | {'At Risk (₹)':<13} | {'Recovered (₹)':<14} | {'Rate %':<6} | {'Avg Conf'}")
    print("  " + "-" * 80)
    for cat in breakdown:
        print(f"  {cat['category']:<28} | {cat['cases_count']:<5} | ₹{cat['at_risk_revenue']:<12,.2f} | ₹{cat['recovered_revenue']:<13,.2f} | {cat['recovery_rate_percentage']:<5.1f}% | {cat['avg_confidence']:.2f}")
    print("=" * 82)
    print("  * NOTICE: All outcomes are computed via a decision-sensitive simulation model.")
    print("  * NO live Razorpay payments or notifications were executed.")
    print("=" * 82 + "\n")


def print_comparison(llm_res: dict, det_res: dict, comp_data: dict):
    m_llm = llm_res["metrics"]
    m_det = det_res["metrics"]
    cmp = comp_data.get("comparison", {})
    cmp_table = comp_data.get("comparison_table", [])

    print("\n" + "#" * 82)
    print("  LLM-ENABLED vs. DETERMINISTIC BENCHMARK COMPARISON & SAFETY AUDIT")
    print("#" * 82)
    print(f"  {'Metric':<36} | {'LLM-Enabled':<18} | {'Deterministic Only':<18}")
    print("  " + "-" * 78)
    print(f"  {'Total Cases Evaluated':<36} | {m_llm['total_cases']:<18} | {m_det['total_cases']:<18}")
    print(f"  {'Total Revenue at Risk':<36} | ₹{m_llm['total_revenue_at_risk']:<17,.2f} | ₹{m_det['total_revenue_at_risk']:<17,.2f}")
    print(f"  {'Simulated Recovered Revenue':<36} | ₹{m_llm['recovered_revenue']:<17,.2f} | ₹{m_det['recovered_revenue']:<17,.2f}")
    print(f"  {'Simulated Recovery Rate (%)':<36} | {m_llm['recovery_rate_percentage']:<17.1f}% | {m_det['recovery_rate_percentage']:<17.1f}%")
    print(f"  {'LLM Decision Count':<36} | {m_llm['llm_decisions']:<18} | {m_det['llm_decisions']:<18}")
    print(f"  {'Fallback Decision Count':<36} | {m_llm['fallback_decisions']:<18} | {m_det['fallback_decisions']:<18}")
    print(f"  {'Human Approvals Required':<36} | {m_llm['human_approvals']:<18} | {m_det['human_approvals']:<18}")
    print(f"  {'Fraud / Security Blocked Cases':<36} | {m_llm['fraud_blocks']:<18} | {m_det['fraud_blocks']:<18}")
    print(f"  {'Auto-Recovered Revenue (<₹50k)':<36} | ₹{m_llm['auto_recovered_revenue']:<17,.2f} | ₹{m_det['auto_recovered_revenue']:<17,.2f}")
    print(f"  {'Human-Approved Revenue':<36} | ₹{m_llm['human_approved_revenue']:<17,.2f} | ₹{m_det['human_approved_revenue']:<17,.2f}")
    print("-" * 82)
    print("  DECISION DIFFERENCES & SAFETY COMPLIANCE:")
    print(f"  Action Difference Count : {cmp.get('action_difference_count', 0)} ({cmp.get('action_difference_percentage', 0.0):.1f}%)")
    print(f"  Fraud Execution Violations : {cmp.get('fraud_auto_execution_violations', 0)} (Target: 0)")
    print(f"  High-Value Bypasses (>=₹50k) : {cmp.get('high_value_approval_bypasses', 0)} (Target: 0)")
    print(f"  Unsupported Actions Count  : {cmp.get('unsupported_actions_count', 0)} (Target: 0)")
    print("-" * 82)
    
    if cmp_table:
        print("  SAMPLE PER-CASE COMPARISON (First 15 Cases):")
        print(f"  {'Case ID':<18} | {'Category':<22} | {'Amount (₹)':<10} | {'Det Action':<12} | {'LLM Action':<12} | {'Diff'}")
        print("  " + "-" * 88)
        for r in cmp_table[:15]:
            diff_str = "YES" if r["action_diff_flag"] else "NO"
            print(f"  {r['case_id']:<18} | {r['category']:<22} | ₹{r['amount']:<9,.0f} | {r['deterministic_action']:<12} | {r['llm_action']:<12} | {diff_str}")
    print("#" * 82 + "\n")


def main():
    parser = argparse.ArgumentParser(description="Run Batch Recovery Evaluation Harness")
    parser.add_argument("--cases", type=int, default=100, help="Number of synthetic cases to evaluate (default: 100)")
    parser.add_argument("--seed", type=int, default=42, help="Deterministic random seed (default: 42)")
    parser.add_argument("--mode", choices=["all", "llm", "deterministic"], default="all", help="Evaluation mode")
    parser.add_argument("--db", type=str, default="data/evaluation.db", help="Path to evaluation SQLite database")
    parser.add_argument("--json-output", type=str, default=None, help="Optional output filepath for machine-readable JSON")

    args = parser.parse_args()

    results_output = {}

    if args.mode == "all":
        eval_result = run_batch_evaluation(
            num_cases=args.cases,
            seed=args.seed,
            mode="all",
            db_path=args.db,
        )
        det_res = eval_result["all_modes"]["deterministic"]
        llm_res = eval_result["all_modes"]["llm"]

        print_evaluation_summary(det_res, title_prefix="[MODE: DETERMINISTIC] ")
        print_evaluation_summary(llm_res, title_prefix="[MODE: LLM-ENABLED] ")
        print_comparison(llm_res, det_res, eval_result)
        results_output = eval_result

    elif args.mode == "deterministic":
        det_result = run_batch_evaluation(
            num_cases=args.cases,
            seed=args.seed,
            mode="deterministic",
            db_path=args.db,
        )
        print_evaluation_summary(det_result, title_prefix="[MODE: DETERMINISTIC] ")
        results_output["deterministic"] = det_result

    elif args.mode == "llm":
        llm_result = run_batch_evaluation(
            num_cases=args.cases,
            seed=args.seed,
            mode="llm",
            db_path=args.db,
        )
        print_evaluation_summary(llm_result, title_prefix="[MODE: LLM-ENABLED] ")
        results_output["llm"] = llm_result

    if args.json_output:
        with open(args.json_output, "w", encoding="utf-8") as f:
            json.dump(results_output, f, indent=2)
        print(f"Saved machine-readable JSON evaluation results to: {args.json_output}")


if __name__ == "__main__":
    main()
