import os
import sys
import requests

BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:8000").rstrip("/")
AGENT_API_KEY = os.getenv("AGENT_API_KEY", "vl_agent_secret_key_abc123")
MERCHANT_USER = os.getenv("MERCHANT_EMAIL") or os.getenv("MERCHANT_USERNAME", "judge_merchant")
MERCHANT_PASSWORD = os.getenv("MERCHANT_PASSWORD", "vaultline_judge_password_123!")

def log_result(check_name: str, passed: bool, detail: str = ""):
    status_str = "\033[92mPASS\033[0m" if passed else "\033[91mFAIL\033[0m"
    if detail:
        print(f"[{status_str}] {check_name}: {detail}")
    else:
        print(f"[{status_str}] {check_name}")
    if not passed:
        sys.exit(1)

def run_live_smoke():
    print(f"Running Vaultline Live Smoke Test against {BASE_URL}...\n")
    agent_headers = {
        "Authorization": f"Bearer {AGENT_API_KEY}",
        "Content-Type": "application/json",
    }

    # 1. Health check
    try:
        r_health = requests.get(f"{BASE_URL}/health", timeout=5)
        log_result("1. Health check GET /health", r_health.status_code == 200 and r_health.json() == {"status": "ok"})
    except Exception as e:
        log_result("1. Health check GET /health", False, f"Connection failed: {e}")

    # 2. Merchant login
    r_login = requests.post(
        f"{BASE_URL}/api/merchant/login",
        json={"username": MERCHANT_USER, "password": MERCHANT_PASSWORD},
        timeout=5,
    )
    login_passed = r_login.status_code == 200 and "session_token" in r_login.json()
    log_result("2. Merchant login POST /api/merchant/login", login_passed)
    session_token = r_login.json().get("session_token")
    merchant_headers = {
        "Authorization": f"Bearer {session_token}",
        "Content-Type": "application/json",
    }

    # 3. Auth matrix basics: Agent key on merchant route -> 403
    r_agent_on_merch = requests.get(f"{BASE_URL}/api/merchant/requests", headers=agent_headers, timeout=5)
    log_result("3. Auth isolation: Agent key gets 403 on merchant route", r_agent_on_merch.status_code == 403)

    # 4. Auth matrix basics: Merchant token on agent route -> 403
    r_merch_on_agent = requests.post(
        f"{BASE_URL}/api/agent/refund-requests",
        json={"capture_id": "CAP_A", "amount": "5.00", "currency": "USD", "action": "refund"},
        headers=merchant_headers,
        timeout=5,
    )
    log_result("4. Auth isolation: Merchant token gets 403 on agent route", r_merch_on_agent.status_code == 403)

    # 5. Demo Step (a): $250 -> BLOCK with adapter_call_count 0
    r_block = requests.post(
        f"{BASE_URL}/api/agent/refund-requests",
        json={"capture_id": "CAP_A", "amount": "250.00", "currency": "USD", "action": "refund"},
        headers=agent_headers,
        timeout=5,
    )
    b_data = r_block.json()
    block_passed = r_block.status_code == 200 and b_data.get("decision") == "BLOCK" and b_data.get("adapter_call_count") == 0
    log_result("5. Demo (a): $250 refund evaluated as BLOCK with adapter_call_count 0", block_passed)

    # 6. Demo Step (b): $20 -> ESCALATE
    r_esc = requests.post(
        f"{BASE_URL}/api/agent/refund-requests",
        json={"capture_id": "CAP_A", "amount": "20.00", "currency": "USD", "action": "refund"},
        headers=agent_headers,
        timeout=5,
    )
    esc_data = r_esc.json()
    esc_passed = r_esc.status_code == 200 and esc_data.get("decision") == "ESCALATE" and esc_data.get("approval_required") is True
    log_result("6. Demo (b): $20 refund evaluated as ESCALATE (approval required)", esc_passed)
    req_esc_id = esc_data.get("request_id")

    # 7. Check merchant queue contains the request
    r_queue = requests.get(f"{BASE_URL}/api/merchant/requests", headers=merchant_headers, timeout=5)
    in_queue = any(r.get("request_id") == req_esc_id for r in r_queue.json())
    log_result("7. Merchant queue contains the escalated request", in_queue)

    # 8. Demo Step (c): Merchant approves $20 -> executes, COMPLETED, refund id exists
    r_app = requests.post(
        f"{BASE_URL}/api/merchant/requests/{req_esc_id}/approve",
        headers=merchant_headers,
        json={"reason": "Live smoke test approved"},
        timeout=5,
    )
    app_data = r_app.json()
    app_passed = (
        r_app.status_code == 200
        and app_data.get("decision") == "ALLOW"
        and app_data.get("execution_status") == "COMPLETED"
        and bool(app_data.get("paypal_refund_id"))
        and app_data.get("adapter_call_count") == 1
    )
    log_result("8. Demo (c): Merchant approves $20 -> COMPLETED with refund ID present", app_passed)

    # 9. Replaying approval on already approved request -> must fail (400 or 409)
    r_re_app = requests.post(
        f"{BASE_URL}/api/merchant/requests/{req_esc_id}/approve",
        headers=merchant_headers,
        timeout=5,
    )
    log_result("9. Replay attack: Re-approving already approved request rejected", r_re_app.status_code in (400, 409))

    # 10. Demo Step (e): $5 -> ALLOW, executes automatically, COMPLETED
    r_allow = requests.post(
        f"{BASE_URL}/api/agent/refund-requests",
        json={"capture_id": "CAP_A", "amount": "5.00", "currency": "USD", "action": "refund"},
        headers=agent_headers,
        timeout=5,
    )
    al_data = r_allow.json()
    allow_passed = (
        r_allow.status_code == 200
        and al_data.get("decision") == "ALLOW"
        and al_data.get("execution_status") == "COMPLETED"
        and bool(al_data.get("paypal_refund_id"))
    )
    log_result("10. Demo (e): $5 refund auto-approved and executed as COMPLETED", allow_passed)

    # 11. Audit chain verification
    r_audit = requests.get(f"{BASE_URL}/api/merchant/audit", headers=merchant_headers, timeout=5)
    audit_data = r_audit.json()
    audit_passed = r_audit.status_code == 200 and audit_data.get("is_valid") is True
    log_result("11. Demo (f): Audit hash chain cryptographically verified", audit_passed, f"{audit_data.get('total_events')} events intact")

    # 12. v2.0 Dashboard summary & risk metrics
    r_sum = requests.get(f"{BASE_URL}/api/v1/dashboard/summary", headers=merchant_headers, timeout=5)
    sum_data = r_sum.json()
    sum_passed = r_sum.status_code == 200 and sum_data.get("total_requests", 0) > 0
    log_result("12. v2.0 Dashboard: GET /api/v1/dashboard/summary live metrics", sum_passed, f"{sum_data.get('total_requests')} total requests")

    r_risk = requests.get(f"{BASE_URL}/api/v1/dashboard/risk", headers=merchant_headers, timeout=5)
    risk_passed = r_risk.status_code == 200 and "high_risk_count" in r_risk.json()
    log_result("13. v2.0 Risk Engine: GET /api/v1/dashboard/risk signals", risk_passed)

    # 14. v2.0 Idempotency enforcement
    idemp_key = f"smoke_idemp_{os.urandom(4).hex()}"
    r_idemp1 = requests.post(
        f"{BASE_URL}/api/v1/refunds",
        json={"capture_id": "CAP_B", "amount": "5.00", "currency": "USD", "client_request_id": idemp_key},
        headers=agent_headers,
        timeout=5,
    )
    r_idemp2 = requests.post(
        f"{BASE_URL}/api/v1/refunds",
        json={"capture_id": "CAP_B", "amount": "5.00", "currency": "USD", "client_request_id": idemp_key},
        headers=agent_headers,
        timeout=5,
    )
    r_idemp_conflict = requests.post(
        f"{BASE_URL}/api/v1/refunds",
        json={"capture_id": "CAP_B", "amount": "8.00", "currency": "USD", "client_request_id": idemp_key},
        headers=agent_headers,
        timeout=5,
    )
    idemp_passed = (
        r_idemp1.status_code == 200
        and r_idemp2.status_code == 200
        and r_idemp1.json().get("request_id") == r_idemp2.json().get("request_id")
        and r_idemp_conflict.status_code == 409
    )
    log_result("14. v2.0 Idempotency: Duplicate key returns existing request; changed payload returns 409", idemp_passed)

    print("\n\033[92mALL LIVE SMOKE TESTS PASSED SUCCESSFULLY!\033[0m")

if __name__ == "__main__":
    run_live_smoke()
