import os
import sys
import json
import urllib.parse
import urllib.request


ENV_FILE = ".env"


def load_env():
    if not os.path.exists(ENV_FILE):
        return

    with open(
        ENV_FILE,
        "r",
        encoding="utf-8-sig",
    ) as f:

        for line in f:
            line = line.strip()

            if not line:
                continue

            if line.startswith("#"):
                continue

            if "=" not in line:
                continue

            key, value = line.split("=", 1)

            key = key.strip()
            value = value.strip()

            if key and key not in os.environ:
                os.environ[key] = value


def send_telegram(message):
    load_env()

    token = os.getenv(
        "TELEGRAM_BOT_TOKEN",
        "",
    ).strip()

    chat_id = os.getenv(
        "TELEGRAM_CHAT_ID",
        "",
    ).strip()

    if not token:
        print(
            "TELEGRAM ERROR: "
            "TELEGRAM_BOT_TOKEN is missing."
        )
        return False

    if not chat_id:
        print(
            "TELEGRAM ERROR: "
            "TELEGRAM_CHAT_ID is missing."
        )
        return False

    url = (
        f"https://api.telegram.org/"
        f"bot{token}/sendMessage"
    )

    payload = urllib.parse.urlencode(
        {
            "chat_id": chat_id,
            "text": message,
            "disable_web_page_preview": "true",
        }
    ).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=payload,
        method="POST",
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=20,
        ) as response:

            raw = response.read().decode(
                "utf-8"
            )

            result = json.loads(raw)

    except Exception as e:
        print(
            "TELEGRAM ERROR:",
            repr(e),
        )
        return False

    if not result.get("ok"):
        print(
            "TELEGRAM ERROR:",
            result,
        )
        return False

    print(
        "Telegram message sent."
    )

    return True


def main():
    message = (
        "✅ LineupAI connected\n\n"
        "Football AI → Telegram is working.\n"
        "AH Agent: ready\n"
        "Master Agent: ready\n"
        "BTTS Shadow: ready"
    )

    ok = send_telegram(
        message
    )

    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()