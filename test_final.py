import asyncio
import re
import aiohttp

async def send_jarvis_notification_async(title: str, message: str):
    # Ensure this token matches BotFather exactly (case-sensitive)
    telegram_token = "8981625642:AAFdXbedkl6cfQ90QS3IKwNK79FcjLafeJo"
    chat_id = "1704700117"

    def escape_markdown(text: str) -> str:
        escape_chars = r'_*[]()~`>#+-=|{}.!'
        return re.sub(f"([{re.escape(escape_chars)}])", r"\\\1", text)

    safe_title = escape_markdown(title)
    safe_message = escape_markdown(message)
    texte_final = f"*{safe_title}*\n\n{safe_message}"
    
    # FIXED: Added api. and /bot to build the correct endpoint
    url = f"https://api.telegram.org/bot{telegram_token}/sendMessage"

    payload = {
        "chat_id": chat_id,
        "text": texte_final,
        "parse_mode": "MarkdownV2",
    }

    try:
        timeout = aiohttp.ClientTimeout(total=5)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload) as response:
                if response.status == 200:
                    print("🎉 SUCCESS: Notification received by Telegram servers!")
                else:
                    res_text = await response.text()
                    print(f"❌ TELEGRAM ERROR {response.status}: {res_text}")
    except Exception as e:
        print(f"❌ NETWORK ERROR: Connection failed. Reason: {e}")

if __name__ == "__main__":
    print("🚀 Starting Telegram test block...")
    asyncio.run(send_jarvis_notification_async("Test Success", "Hello! This is an isolated API bridge check."))