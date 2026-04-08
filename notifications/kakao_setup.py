"""
카카오 나에게 보내기 초기 설정 (최초 1회만 실행)

실행:
    python notifications/kakao_setup.py

준비사항:
    1. https://developers.kakao.com 접속 → 로그인
    2. [내 애플리케이션] → [애플리케이션 추가하기]
    3. 앱 이름 입력 후 저장
    4. [앱 키] 탭에서 REST API 키 복사
    5. [카카오 로그인] → 활성화 ON
    6. [카카오 로그인] → [Redirect URI] → http://localhost 추가
    7. [동의항목] → '카카오톡 메시지 전송' 체크
"""

import sys
import webbrowser
import urllib.parse
from pathlib import Path

import requests
import urllib3
urllib3.disable_warnings()

_ENV_PATH = Path(__file__).parent.parent / ".env"


def _load_env() -> dict:
    env = {}
    if _ENV_PATH.exists():
        for line in _ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def _save_env(updates: dict):
    lines = []
    if _ENV_PATH.exists():
        for line in _ENV_PATH.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                k = stripped.split("=", 1)[0].strip()
                if k in updates:
                    lines.append(f"{k}={updates[k]}")
                    continue
            lines.append(line)
    existing_keys = {l.split("=")[0].strip() for l in lines if "=" in l and not l.strip().startswith("#")}
    for k, v in updates.items():
        if k not in existing_keys:
            lines.append(f"{k}={v}")
    _ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    print("=" * 55)
    print("  카카오 나에게 보내기 초기 설정")
    print("=" * 55)

    env = _load_env()

    existing_key = env.get("KAKAO_REST_API_KEY", "")
    if existing_key and not existing_key.startswith("여기에"):
        print(f"[기존] REST API 키: {existing_key[:8]}...")
        if input("새로 설정하시겠습니까? (y/N): ").strip().lower() != "y":
            rest_api_key = existing_key
        else:
            rest_api_key = input("REST API 키 입력: ").strip()
    else:
        print("developers.kakao.com 에서 REST API 키를 복사하세요.")
        rest_api_key = input("REST API 키 입력: ").strip()

    if not rest_api_key:
        print("[오류] REST API 키가 없습니다.")
        sys.exit(1)

    print("\n[선택] 보안 탭에 Client Secret이 있으면 입력 (없으면 Enter)")
    client_secret = input("Client Secret (없으면 Enter): ").strip()

    auth_url = (
        "https://kauth.kakao.com/oauth/authorize"
        f"?client_id={rest_api_key}"
        "&redirect_uri=http://localhost"
        "&response_type=code"
        "&scope=talk_message"
    )
    print(f"\n[1단계] 브라우저에서 카카오 로그인을 진행합니다...")
    webbrowser.open(auth_url)

    print("[2단계] 로그인 후 브라우저 주소창 URL 전체를 복사하세요.")
    print("  예시: http://localhost/?code=AbCdEf1234567890\n")
    redirect_url = input("리다이렉트 URL 붙여넣기: ").strip()

    parsed = urllib.parse.urlparse(redirect_url)
    params = urllib.parse.parse_qs(parsed.query)
    if "error" in params:
        print(f"[오류] 인증 실패: {params.get('error_description', [''])[0]}")
        sys.exit(1)
    if "code" not in params:
        print("[오류] URL에서 code를 찾을 수 없습니다.")
        sys.exit(1)
    auth_code = params["code"][0]

    print(f"\n[3단계] 액세스 토큰 발급 중...")
    data = {
        "grant_type":   "authorization_code",
        "client_id":    rest_api_key,
        "redirect_uri": "http://localhost",
        "code":         auth_code,
    }
    if client_secret:
        data["client_secret"] = client_secret
    token_data = requests.post("https://kauth.kakao.com/oauth/token",
                               data=data, verify=False, timeout=15).json()

    if "access_token" not in token_data:
        print(f"[오류] 토큰 발급 실패: {token_data}")
        sys.exit(1)

    access_token  = token_data["access_token"]
    refresh_token = token_data.get("refresh_token", "")

    _save_env({
        "KAKAO_REST_API_KEY":  rest_api_key,
        "KAKAO_CLIENT_SECRET": client_secret,
        "KAKAO_ACCESS_TOKEN":  access_token,
        "KAKAO_REFRESH_TOKEN": refresh_token,
    })
    print("[완료] .env 파일에 저장했습니다.")

    print("\n[4단계] 테스트 메시지 전송...")
    from notifications.kakao_notifier import KakaoBot
    bot = KakaoBot(rest_api_key, access_token, refresh_token, client_secret)
    ok  = bot.send_text("✅ 주식 자동매매 카카오톡 연결 테스트 성공!")
    print("[OK] 카카오톡에서 메시지를 확인하세요!" if ok else "[오류] 메시지 전송 실패")

    print("\n" + "=" * 55)
    print("  설정 완료! 이제 python main.py 를 실행하면")
    print("  매수/매도 시 카카오톡으로 알림이 옵니다.")
    print("=" * 55)


if __name__ == "__main__":
    main()
