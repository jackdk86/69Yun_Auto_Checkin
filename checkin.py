import os
import json
import requests
import time
import re
import html
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone

# 转义 HTML 字符，防止 Telegram 报错
def safe_html(text):
    return html.escape(str(text)) if text else ""

# 邮箱脱敏处理
def mask_email(email):
    if "@" not in email:
        return email
    name, domain = email.split("@", 1)
    if len(name) <= 3:
        return f"{name}***@{domain}"
    return f"{name[:3]}***@{domain}"

# 过滤并精简机场的签到返回消息
def clean_checkin_msg(msg):
    if not msg:
        return "签到结果未知"
    
    lines = [line.strip() for line in msg.split('\n') if line.strip()]
    core_keywords = ["获得", "已签到", "签到", "成功", "流量", "续期", "已经"]
    
    for line in lines:
        if any(kw in line for kw in core_keywords):
            if not any(ad in line for ad in ["折", "折扣", "活动时间", "涨价", "优惠"]):
                return line
                
    return lines[0][:50] if lines else "签到成功"

# 解析用户信息
def fetch_and_extract_info(session, domain):
    url = f"{domain}/user"
    try:
        response = session.get(url, timeout=15)
        if response.status_code != 200:
            return "❌ 用户信息页面获取失败\n"
    except Exception as e:
        return f"❌ 请求用户信息失败: {safe_html(e)}\n"

    # --- 1. 提取到期时间和剩余流量 ---
    soup = BeautifulSoup(response.text, 'html.parser')
    script_tags = soup.find_all('script')

    chatra_script = next((script.string for script in script_tags if script.string and 'window.ChatraIntegration' in script.string), None)
    
    user_info = {'到期时间': '未知', '剩余流量': '未知'}
    if chatra_script:
        expire_match = re.search(r"'Class_Expire':\s*'(.*?)'", chatra_script)
        traffic_match = re.search(r"'Unused_Traffic':\s*'(.*?)'", chatra_script)
        
        expire_val = expire_match.group(1) if expire_match else "未知"
        if len(expire_val) > 10:
            expire_val = expire_val.split(" ")[0]  # 只保留日期
            
        user_info['到期时间'] = expire_val
        user_info['剩余流量'] = traffic_match.group(1) if traffic_match else "未知"

    # --- 2. 强力提取订阅链接 (全局扫描算法) ---
    # 匹配 /link/后面跟着字母数字，并且后面有 ?sub=1 或 ?clash=1 的订阅链接特征
    # 兼容单引号、双引号以及任何订阅域名
    sub_links = ""
    sub_match = re.search(r'(https?://[^\'"\s]+/link/[a-zA-Z0-9]+)', response.text)
    
    if sub_match:
        base_link = sub_match.group(1)  # 提取出的基础订阅格式，例如 https://checkhere.top/link/xxxxxx
        clash_link = f"{base_link}?clash=1"
        v2ray_link = f"{base_link}?sub=3"
        sub_links = (
            f"\n🔗 <b>快捷订阅</b>\n"
            f"├ <a href=\"{clash_link}\">⚡ Clash 订阅</a>\n"
            f"└ <a href=\"{v2ray_link}\">🚀 V2ray 订阅</a>"
        )
    else:
        print("⚠️ 未能在页面中匹配到订阅链接")

    info_template = (
        f"📊 <b>用量详情</b>\n"
        f"├ <b>剩余流量</b>: <code>{user_info['剩余流量']}</code>\n"
        f"└ <b>到期时间</b>: <code>{user_info['到期时间']}</code>\n"
        f"{sub_links}"
    )
    return info_template

# 读取环境变量
def generate_config():
    domain = os.getenv('AIRPORT_DOMAIN', 'https://69yun69.com')
    bot_token = os.getenv('BOT_TOKEN', '').strip()
    chat_id = os.getenv('CHAT_ID', '').strip()
    
    accounts = []
    index = 1
    while True:
        user, password = os.getenv(f'USER{index}'), os.getenv(f'PASS{index}')
        if not user or not password:
            break
        accounts.append({'user': user.strip(), 'pass': password.strip()})
        index += 1

    return {'domain': domain, 'BotToken': bot_token, 'ChatID': chat_id, 'accounts': accounts}

# 发送 Telegram 消息
def send_message(msg, bot_token, chat_id):
    if not bot_token or not chat_id:
        print("⚠️ 提示: 未配置 BOT_TOKEN 或 CHAT_ID，跳过 Telegram 推送！")
        return
    
    tz_utc_8 = timezone(timedelta(hours=8))
    now = datetime.now(tz_utc_8)
    
    payload = {
        "chat_id": chat_id,
        "text": f"⏰ <b>执行时间</b>: {now.strftime('%Y-%m-%d %H:%M:%S')}\n\n{msg}",
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    try:
        res = requests.post(f"https://api.telegram.org/bot{bot_token}/sendMessage", data=payload, timeout=15)
        if res.status_code != 200:
            print(f"❌ Telegram 推送失败，API 返回状态码: {res.status_code}")
            print(f"❌ 错误详情: {res.text}")
        else:
            print("🚀 Telegram 消息推送成功！")
    except Exception as e:
        print(f"❌ 发送 Telegram 消息出错: {e}")

# 登录并签到
def checkin(account, domain, bot_token, chat_id):
    user, password = account['user'], account['pass']
    masked_user = mask_email(user)
    
    account_header = f"👤 <b>账号信息</b>\n├ <b>账号</b>: <code>{masked_user}</code>\n"

    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/129.0.0.0 Safari/537.36',
        'Accept': 'application/json',
    })

    # 登录
    try:
        login_response = session.post(
            f"{domain}/auth/login",
            json={'email': user, 'passwd': password, 'remember_me': 'on', 'code': ""},
            headers={
                'Content-Type': 'application/json',
                'Origin': domain,
                'Referer': f"{domain}/auth/login",
            },
            timeout=15
        )
        login_res_json = login_response.json()
    except Exception as e:
        err_msg = f"└ <b>状态</b>: ❌ 登录请求失败 ({safe_html(e)})\n"
        send_message(account_header + err_msg, bot_token, chat_id)
        return err_msg

    if login_response.status_code != 200 or login_res_json.get("ret") != 1:
        err_msg = f"└ <b>状态</b>: ❌ 登录失败 ({safe_html(login_res_json.get('msg', '未知错误'))})\n"
        send_message(account_header + err_msg, bot_token, chat_id)
        return err_msg

    time.sleep(1)

    # 签到
    try:
        checkin_response = session.post(
            f"{domain}/user/checkin",
            headers={
                'Origin': domain,
                'Referer': f"{domain}/user/panel"
            },
            timeout=15
        )
        checkin_result = checkin_response.json() if checkin_response.status_code == 200 else {}
    except Exception as e:
        checkin_result = {}
        print(f"⚠️ 签到请求失败: {e}")

    raw_msg = checkin_result.get('msg', '签到结果未知(可能已签到)')
    cleaned_msg = clean_checkin_msg(raw_msg)
    result_emoji = "⚠️" if "已经" in cleaned_msg or "过" in cleaned_msg else "🎉"
    
    status_msg = f"└ <b>状态</b>: {result_emoji} <b>{safe_html(cleaned_msg)}</b>\n\n"

    # 获取用量信息
    user_info = fetch_and_extract_info(session, domain)
    
    final_msg = f"{account_header}{status_msg}{user_info}"
    send_message(final_msg, bot_token, chat_id)
    return final_msg

# 主函数
if __name__ == "__main__":
    config = generate_config()
    if not config["accounts"]:
        print("❌ 未在环境变量中找到任何账号配置 (USER1, PASS1...)")
    for account in config.get("accounts", []):
        print(f"📌 正在为 {mask_email(account['user'])} 签到...")
        checkin(account, config['domain'], config['BotToken'], config['ChatID'])
