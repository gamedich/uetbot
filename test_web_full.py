#!/usr/bin/env python3
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = "http://127.0.0.1:8082"

def post_json(path, data, token=None):
    url = f"{BASE_URL}{path}"
    req = urllib.request.Request(
        url,
        data=json.dumps(data).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            **({"Cookie": f"uet_token={token}"} if token else {})
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, body
    except Exception as e:
        return 0, str(e)

def get_json(path, token=None):
    url = f"{BASE_URL}{path}"
    req = urllib.request.Request(
        url,
        headers={"Cookie": f"uet_token={token}"} if token else {}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, body
    except Exception as e:
        return 0, str(e)

def get_html(path):
    url = f"{BASE_URL}{path}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return resp.status, resp.read().decode("utf-8")
    except Exception as e:
        return 0, str(e)

def run_tests():
    print("=== STARTING FULL WEB APP TESTS ===")
    results = {}

    # 1. Page routes
    for page in ["/", "/apply", "/dashboard", "/showcase", "/kadry", "/docs", "/api/health"]:
        status, content = get_html(page)
        ok = status in (200, 302)
        results[f"PAGE {page}"] = "PASS" if ok else f"FAIL ({status})"
        print(f"[*] Page {page}: {status}")

    # 2. Public API: Vacancies
    status, data = get_json("/api/vacancies")
    ok = status == 200 and isinstance(data, (list, dict))
    results["API /api/vacancies"] = "PASS" if ok else f"FAIL ({status})"
    vac_cnt = len(data) if isinstance(data, list) else len(data.get('vacancies', [])) if isinstance(data, dict) else 0
    print(f"[*] Vacancies: {status}, count={vac_cnt}")

    # 3. Public API: Candidate status lookup
    status, data = get_json("/api/candidate/lookup?ticket_id=900005")
    print(f"[*] Candidate Lookup: {status}, data={data}")
    results["API /api/candidate/lookup"] = "PASS" if status in (200, 404) else f"FAIL ({status})"

    # 4. Public API: Question submission & check
    q_data = {
        "candidate_id": 900005,
        "full_name": "Тестовый Соискатель",
        "phone": "+79991112233",
        "question": "Тестовый вопрос для проверки работы формы обратной связи"
    }
    status, data = post_json("/api/inquiry/submit", q_data)
    print(f"[*] Question submit: {status}, data={data}")
    inquiry_id = data.get("inquiry_id") if isinstance(data, dict) else None
    results["API /api/inquiry/submit"] = "PASS" if status == 200 and inquiry_id else f"FAIL ({status})"

    if inquiry_id:
        status, data = get_json(f"/api/inquiry/check?inquiry_id={inquiry_id}")
        print(f"[*] Question check: {status}, data={data}")
        results["API /api/inquiry/check"] = "PASS" if status == 200 else f"FAIL ({status})"

    # 5. Candidate Apply (16-step survey)
    app_data = {
        "full_name": "Тестов Иван Иванович",
        "phone": "+79991234567",
        "vacancy": "Водитель трамвая",
        "age": 28,
        "education": "Среднее специальное",
        "experience": "Да, водительский стаж 5 лет",
        "driver_license": "B, C",
        "citizenship": "РФ",
        "military_status": "Военнообязанный",
        "city_district": "Ленинский район",
        "depot_preference": "Заволжское депо №2",
        "schedule_preference": "2/2",
        "ready_for_study": "Да, готов",
        "medical_restrictions": "Нет",
        "source": "Веб-портал",
        "notes": "Автоматический тест полного цикла"
    }
    status, data = post_json("/api/apply_candidate", app_data)
    print(f"[*] Candidate apply: {status}, data={data}")
    test_ticket = data.get("ticket_id") if isinstance(data, dict) else None
    results["API /api/apply_candidate"] = "PASS" if status == 200 and test_ticket else f"FAIL ({status})"

    # 6. Auth Security: OTP Request & Verification
    # First, test OTP request for SuperAdmin (7657422832)
    status, data = post_json("/api/auth/request_otp", {"target": "7657422832", "role": "superadmin"})
    print(f"[*] OTP Request (7657422832): {status}, data={data}")
    results["API /api/auth/request_otp"] = "PASS" if status == 200 and data.get("ok") else f"FAIL ({status})"

    dev_code = data.get("dev_code") if isinstance(data, dict) else None
    super_token = None
    if dev_code:
        status, data = post_json("/api/auth/verify_otp", {"target": "7657422832", "code": dev_code})
        print(f"[*] OTP Verify (7657422832): {status}, ok={data.get('ok')}")
        results["API /api/auth/verify_otp"] = "PASS" if status == 200 and data.get("token") else f"FAIL ({status})"
        super_token = data.get("token")

    # HR token via OTP or fallback
    hr_token = super_token

    # 7. Authenticated: Candidates List
    status, data = get_json("/api/candidates", token=super_token)
    print(f"[*] SuperAdmin /api/candidates: {status}, count={len(data.get('candidates', [])) if isinstance(data, dict) else len(data) if isinstance(data, list) else 0}")
    results["API /api/candidates (Auth)"] = "PASS" if status == 200 else f"FAIL ({status})"

    # 8. Authenticated: Candidate Status Update (Kanban drag & drop)
    if test_ticket:
        status, data = post_json(f"/api/candidate/{test_ticket}/status", {"status": "interview"}, token=hr_token)
        print(f"[*] Update candidate status to interview: {status}, data={data}")
        results["API /api/candidate/{id}/status (Kanban drag)"] = "PASS" if status == 200 else f"FAIL ({status})"

        # Add HR note
        status, data = post_json(f"/api/candidate/{test_ticket}/note", {"note": "Кандидат успешно протестирован"}, token=hr_token)
        print(f"[*] Add candidate note: {status}, data={data}")
        results["API /api/candidate/{id}/note"] = "PASS" if status == 200 else f"FAIL ({status})"

    # 9. Tech Stats & Logs
    status, data = get_json("/api/system", token=super_token)
    print(f"[*] Tech /api/system: {status}, ok={data.get('ok') if isinstance(data, dict) else False}")
    results["API /api/system (Tech)"] = "PASS" if status == 200 else f"FAIL ({status})"

    status, data = get_json("/api/system/logs", token=super_token)
    print(f"[*] Tech /api/system/logs: {status}")
    results["API /api/system/logs"] = "PASS" if status == 200 else f"FAIL ({status})"

    # 10. Admins Management
    status, data = get_json("/api/admins", token=super_token)
    print(f"[*] SuperAdmin /api/admins: {status}, count={len(data.get('admins', [])) if isinstance(data, dict) else 0}")
    results["API /api/admins"] = "PASS" if status == 200 else f"FAIL ({status})"

    # 11. Cooldown Setting
    status, data = post_json("/api/super/set_cooldown", {"seconds": 30}, token=super_token)
    print(f"[*] SuperAdmin /api/super/set_cooldown: {status}, data={data}")
    results["API /api/super/set_cooldown"] = "PASS" if status == 200 and data.get("ok") else f"FAIL ({status})"

    # 12. Maintenance Mode
    status, data = get_json("/api/system/maintenance", token=super_token)
    print(f"[*] Tech /api/system/maintenance get: {status}, data={data}")
    results["API /api/system/maintenance"] = "PASS" if status == 200 else f"FAIL ({status})"

    print("\n=== SUMMARY OF TESTS ===")
    all_passed = True
    for test_name, res in results.items():
        print(f" - {test_name}: {res}")
        if not res.startswith("PASS"):
            all_passed = False

    print(f"\nOVERALL RESULT: {'ALL TESTS PASSED' if all_passed else 'SOME TESTS FAILED'}")
    return 0 if all_passed else 1

if __name__ == '__main__':
    sys.exit(run_tests())
