import os
import time
import json
import urllib.request
import urllib.parse
import re
from seleniumbase import SB

# --- 账号配置 ---
_account = os.environ.get("MINESTRATOR_ACCOUNT", "").split(",")
EMAIL      = _account[0].strip() if len(_account) > 0 else ""
PASSWORD   = _account[1].strip() if len(_account) > 1 else ""
SERVER_ID  = os.environ.get("MINESTRATOR_SERVER_ID", "").strip()
AUTH_TOKEN = os.environ.get("MINESTRATOR_AUTH", "").strip()

# --- 代理配置修改：强制指向本地 Xray 转发端口 ---
# 无论环境变量如何，优先尝试连接本地 8080 端口
LOCAL_PROXY = "http://127.0.0.1:8080"

_tg = os.environ.get("TG_BOT", "").strip()
TG_CHAT_ID = _tg.split(",")[0].strip() if _tg else ""
TG_TOKEN   = _tg.split(",")[1].strip() if _tg and "," in _tg else ""

LOGIN_URL  = "https://minestrator.com/connexion"
SERVER_URL = f"https://minestrator.com/my/server/{SERVER_ID}"
API_URL    = f"https://mine.sttr.io/server/{SERVER_ID}/poweraction"

# ============================================================
# TG 推送
# ============================================================

def now_str():
    import datetime
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

def send_tg(result, detail=''):
    if not TG_TOKEN or not TG_CHAT_ID:
        print("ℹ️ 未配置 TG_BOT，跳过推送")
        return
    msg = (
        f"🎮 Minestrator 重启通知\n"
        f"🕐 运行时间: {now_str()}\n"
        f"🖥 服务器: 🇫🇷 Minestrator-FR\n"
        f"📊 结果: {result}\n"
        f"{detail}"
    )
    url  = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": TG_CHAT_ID, "text": msg}).encode()
    try:
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=15):
            print("📨 TG推送成功")
    except Exception as e:
        print(f"⚠️ TG推送失败：{e}")

# ============================================================
# Invisible Turnstile 逻辑 (保持不变)
# ============================================================

INJECT_TOKEN_LISTENER_JS = """
(function() {
    if (window.__cf_token_listener_injected__) return;
    window.__cf_token_listener_injected__ = true;
    window.__cf_turnstile_token__ = '';
    window.addEventListener('message', function(e) {
        if (!e.origin || e.origin.indexOf('cloudflare.com') === -1) return;
        var d = e.data;
        if (!d || d.event !== 'complete' || !d.token) return;
        console.log('[TokenCapture] complete, token length:', d.token.length);
        window.__cf_turnstile_token__ = d.token;
        var inputs = document.querySelectorAll('input[name="cf-turnstile-response"], input[name="cf_turnstile_response"]');
        for (var i = 0; i < inputs.length; i++) {
            try {
                var nativeSet = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
                nativeSet.call(inputs[i], d.token);
                inputs[i].dispatchEvent(new Event('input', {bubbles: true}));
                inputs[i].dispatchEvent(new Event('change', {bubbles: true}));
            } catch(err) { inputs[i].value = d.token; }
        }
    });
})();
"""

def inject_listener(sb):
    try:
        sb.execute_script(INJECT_TOKEN_LISTENER_JS)
        print("📡 Turnstile 监听器已注入")
    except Exception as e: print(f"⚠️ 监听器注入失败：{e}")

def wait_for_token(sb, timeout=60) -> str:
    print(f"⏳ 等待 Turnstile Token (最多 {timeout} 秒)...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            token = sb.execute_script("return window.__cf_turnstile_token__ || '';")
            if token and len(token) > 50: return token
        except: pass
        time.sleep(1)
    return ''

# ============================================================
# API 重启指令 (保持不变)
# ============================================================

def send_restart(sb, token: str) -> bool:
    token_js = json.dumps(token)
    script = (
        "var done = arguments[0];"
        'fetch("' + API_URL + '", {'
        '  method: "PUT",'
        '  headers: {'
        '    "Authorization": "' + AUTH_TOKEN + '",'
        '    "Content-Type": "application/json",'
        '    "Accept": "application/json",'
        '    "X-Requested-With": "XMLHttpRequest"'
        '  },'
        '  body: JSON.stringify({poweraction: "restart", turnstile_token: ' + token_js + '})'
        '})'
        '.then(function(r){ return r.json(); })'
        '.then(function(data){ done({ok: true, data: data}); })'
        '.catch(function(err){ done({ok: false, error: err.toString()}); });'
    )
    try:
        result = sb.execute_async_script(script)
        if result.get("ok") and result.get("data", {}).get("api", {}).get("code") == 200:
            return True
        return False
    except Exception as e:
        print(f"⚠️ API异常：{e}")
        return False

# ============================================================
# 主流程
# ============================================================

def run_script():
    print("🔧 启动浏览器（强制代理模式）...")

    # 强制开启 uc 模式以绕过检测，并指定本地 8080 代理
    sb_kwargs = {
        "uc": True,
        "test": True,
        "headless": True,
        "proxy": LOCAL_PROXY
    }
    
    with SB(**sb_kwargs) as sb:
        print(f"🌐 代理已配置：{LOCAL_PROXY}")

        # ── IP 验证 ──────────────────────────────────────────
        print("🌐 验证代理出口IP...")
        try:
            sb.open("https://api.ipify.org/?format=json")
            ip_data = sb.get_text('body')
            print(f"✅ 当前出口数据：{ip_data}")
        except Exception:
            print("⚠️ IP验证失败，请确认 Xray 节点是否可用")

        # ── 登录 ─────────────────────────────────────────────
        print("🔑 正在打开登录页面...")
        sb.uc_open_with_reconnect(LOGIN_URL, reconnect_time=4)
        time.sleep(5)

        try:
            sb.wait_for_element_visible("input[name='pseudo']", timeout=20)
            sb.type("input[name='pseudo']", EMAIL)
            sb.type("input[name='password']", PASSWORD)
            sb.click("button[type='submit']")
            print("📤 登录信息已提交")
        except Exception:
            print("❌ 登录交互失败")
            sb.save_screenshot("login_error.png")
            return

        # 等待跳转
        time.sleep(5)

        # ── 跳转服务器管理页 ──────────────────────────────────
        print(f"🔃 跳转至管理页：{SERVER_URL}")
        sb.open(SERVER_URL)
        time.sleep(5)
        
        inject_listener(sb)
        token = wait_for_token(sb, timeout=60)
        
        if not token:
            print("❌ 未能获取 Turnstile Token")
            sb.save_screenshot("token_error.png")
            send_tg("❌ 重启失败", "验证码校验超时")
            return

        # ── 发送重启指令 ──────────────────────────────────────
        if send_restart(sb, token):
            print("✅ 重启成功！")
            # 尝试抓取剩余时间
            time.sleep(5)
            sb.open(SERVER_URL)
            try:
                remaining = sb.execute_script("return document.querySelector('[data-slot=\"base\"] span')?.textContent || ''")
                send_tg("✅ 重启成功！", f"⏰ 期限：{remaining}")
            except:
                send_tg("✅ 重启成功！", "无法解析具体期限")
        else:
            print("❌ 重启指令发送失败")
            send_tg("❌ 重启失败", "API 响应异常")

if __name__ == "__main__":
    run_script()
