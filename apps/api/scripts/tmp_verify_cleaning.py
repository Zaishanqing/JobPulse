import httpx


base = "http://localhost:8000/api/v1"
with httpx.Client(base_url=base, timeout=30.0) as client:
    login = client.post(
        "/auth/login",
        json={"username": "demo_admin", "password": "password123"},
    ).json()
    headers = {"Authorization": f"Bearer {login['data']['access_token']}"}
    jd = client.get(
        "/jds/1dcf84f7-76b6-4492-b473-ebcb05d5ddf6",
        headers=headers,
    ).json()["data"]["raw_text"]
    ctx = client.get(
        "/review-tasks/0b3ecc1a-fefb-427a-ad0f-29274daf6896/context",
        headers=headers,
    ).json()["data"]["raw_text"]
    for name, text in [("jd", jd), ("context", ctx)]:
        assert "kanzhun" not in text, f"{name} still has kanzhun"
        assert "来自BOSS直聘" not in text, f"{name} still has 来自BOSS直聘"
        assert "岗位职责" in text, f"{name} missing 岗位职责"
        assert "定义AI产品" in text, f"{name} missing 定义AI产品"
        start = text.find("1、")
        print(name, "->", text[start : start + 30])
