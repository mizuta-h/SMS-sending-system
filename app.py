#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SMS送信システム - Webダッシュボード
Flask ベースのモダンなUIアプリケーション
"""

from flask import Flask, render_template, request, jsonify, send_file, Response
import csv
import json
import subprocess
import time
import os
import threading
import queue
from datetime import datetime
from pathlib import Path
import urllib.parse
import io

app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False

# ベースディレクトリ
BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config.json"
CSV_PATH = BASE_DIR / "contacts.csv"
LOG_DIR = BASE_DIR / "logs"

# 送信状態を管理
send_status = {
    "is_running": False,
    "current": 0,
    "total": 0,
    "results": [],
    "start_time": None
}

# ログキュー（リアルタイム表示用）
log_queue = queue.Queue()


def load_config():
    """設定ファイルを読み込む"""
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            config = json.load(f)
            # デフォルト値を追加
            config.setdefault('send_method', 'tap')
            config.setdefault('send_button_x', 980)
            config.setdefault('send_button_y', 1850)
            config.setdefault('max_send_count', 0)  # 0 = unlimited
            config.setdefault('daily_sent_count', 0)
            config.setdefault('daily_sent_date', '')
            return config
    return {
        "default_message": "This is a reminder message.",
        "adb_path": "C:\\platform-tools\\adb.exe",
        "send_delay_seconds": 5,
        "retry_count": 3,
        "scheduled_time": "09:00",
        "dry_run": False,
        "send_method": "tap",
        "send_button_x": 980,
        "send_button_y": 1850,
        "max_send_count": 0,
        "daily_sent_count": 0,
        "daily_sent_date": ""
    }


def save_config(config):
    """設定ファイルを保存"""
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=4)


def load_contacts():
    """CSVから連絡先を読み込む"""
    contacts = []
    if CSV_PATH.exists():
        with open(CSV_PATH, 'r', encoding='utf-8') as f:
            lines = [l for l in f if not l.startswith('#')]
            reader = csv.DictReader(lines)
            for i, row in enumerate(reader):
                contacts.append({
                    'id': i,
                    'phone': row.get('phone', '').strip(),
                    'name': row.get('name', '').strip(),
                    'message': row.get('message', '').strip(),
                    'enabled': row.get('enabled', '1').strip() == '1'
                })
    return contacts


def save_contacts(contacts):
    """CSVに連絡先を保存"""
    with open(CSV_PATH, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['phone', 'name', 'message', 'enabled'])
        writer.writeheader()
        for c in contacts:
            writer.writerow({
                'phone': c['phone'],
                'name': c['name'],
                'message': c['message'],
                'enabled': '1' if c.get('enabled', True) else '0'
            })


def run_adb_command(command):
    """ADBコマンドを実行"""
    config = load_config()
    adb_path = config.get('adb_path', 'adb')
    full_command = f'"{adb_path}" {command}'
    
    try:
        result = subprocess.run(
            full_command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30
        )
        return result.returncode == 0, result.stdout, result.stderr
    except Exception as e:
        return False, "", str(e)


def check_device():
    """デバイス接続確認"""
    success, stdout, stderr = run_adb_command("devices")
    if not success:
        return False, "ADBが実行できません", None
    
    lines = stdout.strip().split('\n')
    devices = [l for l in lines[1:] if l.strip() and 'device' in l]
    
    if not devices:
        return False, "デバイスが接続されていません", None
    
    device_id = devices[0].split()[0]
    return True, f"接続済み: {device_id}", device_id


def send_sms(phone, message, dry_run=False):
    """SMS送信"""
    if dry_run:
        time.sleep(0.5)
        return True, "Dry run OK"
    
    config = load_config()
    
    # インテントでSMSアプリを起動
    escaped_message = message.replace('"', '\\"').replace("'", "\\'")
    intent_cmd = f'shell am start -a android.intent.action.SENDTO -d "sms:{phone}" --es sms_body "{escaped_message}"'
    success, stdout, stderr = run_adb_command(intent_cmd)
    
    if not success:
        return False, f"Launch failed: {stderr}"
    
    # アプリ起動を待つ
    time.sleep(3)
    
    # 送信方法を取得（設定から）
    send_method = config.get('send_method', 'tap')
    tap_x = config.get('send_button_x', 980)
    tap_y = config.get('send_button_y', 1850)
    
    if send_method == 'tap':
        # 画面タップで送信ボタンを押す
        run_adb_command(f"shell input tap {tap_x} {tap_y}")
        time.sleep(1)
        # もう一度タップ（確認ダイアログ対応）
        run_adb_command(f"shell input tap {tap_x} {tap_y}")
    elif send_method == 'key':
        # キーイベントで送信
        run_adb_command("shell input keyevent 66")  # Enter
        time.sleep(0.5)
        run_adb_command("shell input keyevent 66")  # Enter again
    elif send_method == 'tab_enter':
        # Tabで送信ボタンにフォーカス→Enter
        run_adb_command("shell input keyevent 61")  # Tab
        time.sleep(0.3)
        run_adb_command("shell input keyevent 61")  # Tab
        time.sleep(0.3)
        run_adb_command("shell input keyevent 66")  # Enter
    
    time.sleep(2)
    
    # ホームに戻る
    run_adb_command("shell input keyevent 3")  # Home
    
    return True, "Sent"


def send_all_sms(dry_run=False):
    """全SMS送信（バックグラウンド実行）"""
    global send_status
    
    config = load_config()
    contacts = [c for c in load_contacts() if c.get('enabled', True)]
    default_message = config.get('default_message', '')
    delay = config.get('send_delay_seconds', 5)
    max_count = config.get('max_send_count', 0)  # 0 = unlimited
    
    # 今日の日付
    today = datetime.now().strftime('%Y-%m-%d')
    
    # 日付が変わったらカウントリセット
    if config.get('daily_sent_date', '') != today:
        config['daily_sent_count'] = 0
        config['daily_sent_date'] = today
        save_config(config)
    
    daily_sent = config.get('daily_sent_count', 0)
    
    # 送信可能件数を計算
    if max_count > 0:
        remaining = max_count - daily_sent
        if remaining <= 0:
            send_status = {
                "is_running": False,
                "current": 0,
                "total": 0,
                "results": [],
                "start_time": datetime.now().isoformat(),
                "error": f"Daily limit reached ({max_count} SMS). Resets tomorrow."
            }
            return
        # 送信対象を制限
        contacts = contacts[:remaining]
    
    send_status = {
        "is_running": True,
        "current": 0,
        "total": len(contacts),
        "results": [],
        "start_time": datetime.now().isoformat(),
        "max_count": max_count,
        "daily_sent_before": daily_sent
    }
    
    LOG_DIR.mkdir(exist_ok=True)
    log_file = LOG_DIR / f"sms_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    
    sent_count = 0
    
    for i, contact in enumerate(contacts):
        if not send_status["is_running"]:
            break
        
        send_status["current"] = i + 1
        message = contact['message'] or default_message
        
        log_entry = {
            "index": i + 1,
            "phone": contact['phone'],
            "name": contact['name'],
            "timestamp": datetime.now().isoformat()
        }
        
        success, result = send_sms(contact['phone'], message, dry_run)
        log_entry["success"] = success
        log_entry["result"] = result
        
        send_status["results"].append(log_entry)
        log_queue.put(log_entry)
        
        if success:
            sent_count += 1
        
        if i < len(contacts) - 1 and send_status["is_running"]:
            time.sleep(delay)
    
    send_status["is_running"] = False
    
    # 日次カウントを更新（ドライラン以外）
    if not dry_run:
        config = load_config()
        config['daily_sent_count'] = config.get('daily_sent_count', 0) + sent_count
        config['daily_sent_date'] = today
        save_config(config)
    
    # ログファイル保存
    with open(log_file, 'w', encoding='utf-8') as f:
        json.dump(send_status, f, ensure_ascii=False, indent=2)


# ===== ルーティング =====

@app.route('/')
def index():
    """メインダッシュボード"""
    return render_template('index.html')


@app.route('/api/config', methods=['GET', 'POST'])
def api_config():
    """設定の取得/更新"""
    if request.method == 'GET':
        return jsonify(load_config())
    else:
        config = request.json
        save_config(config)
        return jsonify({"success": True})


@app.route('/api/contacts', methods=['GET'])
def api_get_contacts():
    """連絡先一覧取得"""
    return jsonify(load_contacts())


@app.route('/api/contacts', methods=['POST'])
def api_add_contact():
    """連絡先追加"""
    contacts = load_contacts()
    new_contact = request.json
    new_contact['id'] = len(contacts)
    contacts.append(new_contact)
    save_contacts(contacts)
    return jsonify({"success": True, "contact": new_contact})


@app.route('/api/contacts/<int:contact_id>', methods=['PUT'])
def api_update_contact(contact_id):
    """連絡先更新"""
    contacts = load_contacts()
    for c in contacts:
        if c['id'] == contact_id:
            c.update(request.json)
            break
    save_contacts(contacts)
    return jsonify({"success": True})


@app.route('/api/contacts/<int:contact_id>', methods=['DELETE'])
def api_delete_contact(contact_id):
    """連絡先削除"""
    contacts = load_contacts()
    contacts = [c for c in contacts if c['id'] != contact_id]
    # IDを振り直す
    for i, c in enumerate(contacts):
        c['id'] = i
    save_contacts(contacts)
    return jsonify({"success": True})


@app.route('/api/contacts/bulk', methods=['POST'])
def api_bulk_contacts():
    """連絡先一括操作"""
    action = request.json.get('action')
    ids = request.json.get('ids', [])
    contacts = load_contacts()
    
    if action == 'enable':
        for c in contacts:
            if c['id'] in ids:
                c['enabled'] = True
    elif action == 'disable':
        for c in contacts:
            if c['id'] in ids:
                c['enabled'] = False
    elif action == 'delete':
        contacts = [c for c in contacts if c['id'] not in ids]
        for i, c in enumerate(contacts):
            c['id'] = i
    
    save_contacts(contacts)
    return jsonify({"success": True})


@app.route('/api/contacts/import', methods=['POST'])
def api_import_contacts():
    """CSVインポート"""
    if 'file' not in request.files:
        return jsonify({"success": False, "error": "ファイルがありません"})
    
    file = request.files['file']
    content = file.read().decode('utf-8')
    
    contacts = load_contacts()
    lines = [l for l in content.split('\n') if l.strip() and not l.startswith('#')]
    
    if lines:
        reader = csv.DictReader(lines)
        imported = 0
        for row in reader:
            if row.get('phone'):
                contacts.append({
                    'id': len(contacts),
                    'phone': row.get('phone', '').strip(),
                    'name': row.get('name', '').strip(),
                    'message': row.get('message', '').strip(),
                    'enabled': row.get('enabled', '1').strip() == '1'
                })
                imported += 1
        
        save_contacts(contacts)
        return jsonify({"success": True, "imported": imported})
    
    return jsonify({"success": False, "error": "データがありません"})


@app.route('/api/contacts/export')
def api_export_contacts():
    """CSVエクスポート"""
    contacts = load_contacts()
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=['phone', 'name', 'message', 'enabled'])
    writer.writeheader()
    for c in contacts:
        writer.writerow({
            'phone': c['phone'],
            'name': c['name'],
            'message': c['message'],
            'enabled': '1' if c.get('enabled', True) else '0'
        })
    
    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={"Content-Disposition": f"attachment;filename=contacts_{datetime.now().strftime('%Y%m%d')}.csv"}
    )


@app.route('/api/device/check')
def api_check_device():
    """デバイス接続確認"""
    connected, message, device_id = check_device()
    return jsonify({
        "connected": connected,
        "message": message,
        "device_id": device_id
    })


@app.route('/api/test/tap', methods=['POST'])
def api_test_tap():
    """タップテスト"""
    x = request.json.get('x', 980)
    y = request.json.get('y', 1850)
    
    success, stdout, stderr = run_adb_command(f"shell input tap {x} {y}")
    
    return jsonify({
        "success": success,
        "error": stderr if not success else None
    })


@app.route('/api/screen/size')
def api_screen_size():
    """画面サイズを取得"""
    success, stdout, stderr = run_adb_command("shell wm size")
    if success and 'Physical size:' in stdout:
        # "Physical size: 1080x2400" のような形式
        size_str = stdout.split('Physical size:')[1].strip()
        parts = size_str.split('x')
        if len(parts) == 2:
            return jsonify({
                "success": True,
                "width": int(parts[0]),
                "height": int(parts[1])
            })
    return jsonify({"success": False, "error": "Could not get screen size"})


@app.route('/api/screen/screenshot')
def api_screenshot():
    """スクリーンショットを取得"""
    import base64
    
    # スクリーンショットを撮影してbase64で返す
    run_adb_command("shell screencap -p /sdcard/screenshot.png")
    success, stdout, stderr = run_adb_command("exec-out cat /sdcard/screenshot.png")
    
    if success:
        # バイナリデータを取得
        try:
            result = subprocess.run(
                f'"{load_config().get("adb_path", "adb")}" exec-out cat /sdcard/screenshot.png',
                shell=True,
                capture_output=True,
                timeout=30
            )
            if result.returncode == 0:
                img_base64 = base64.b64encode(result.stdout).decode('utf-8')
                return jsonify({
                    "success": True,
                    "image": f"data:image/png;base64,{img_base64}"
                })
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})
    
    return jsonify({"success": False, "error": "Screenshot failed"})


@app.route('/api/send/start', methods=['POST'])
def api_start_send():
    """送信開始"""
    global send_status
    
    if send_status["is_running"]:
        return jsonify({"success": False, "error": "送信中です"})
    
    dry_run = request.json.get('dry_run', False)
    
    # バックグラウンドで実行
    thread = threading.Thread(target=send_all_sms, args=(dry_run,))
    thread.daemon = True
    thread.start()
    
    return jsonify({"success": True})


@app.route('/api/send/stop', methods=['POST'])
def api_stop_send():
    """送信停止"""
    global send_status
    send_status["is_running"] = False
    return jsonify({"success": True})


@app.route('/api/send/status')
def api_send_status():
    """送信状態取得"""
    return jsonify(send_status)


@app.route('/api/send/stream')
def api_send_stream():
    """送信ログのSSE配信"""
    def generate():
        while True:
            try:
                entry = log_queue.get(timeout=1)
                yield f"data: {json.dumps(entry, ensure_ascii=False)}\n\n"
            except queue.Empty:
                yield f"data: {json.dumps({'ping': True})}\n\n"
    
    return Response(generate(), mimetype='text/event-stream')


@app.route('/api/logs')
def api_get_logs():
    """ログ一覧取得"""
    logs = []
    if LOG_DIR.exists():
        for f in sorted(LOG_DIR.glob('*.json'), reverse=True)[:20]:
            try:
                with open(f, 'r', encoding='utf-8') as file:
                    data = json.load(file)
                    logs.append({
                        "filename": f.name,
                        "start_time": data.get("start_time"),
                        "total": data.get("total", 0),
                        "success": sum(1 for r in data.get("results", []) if r.get("success")),
                        "failed": sum(1 for r in data.get("results", []) if not r.get("success"))
                    })
            except:
                pass
    return jsonify(logs)


@app.route('/api/logs/<filename>')
def api_get_log_detail(filename):
    """ログ詳細取得"""
    log_file = LOG_DIR / filename
    if log_file.exists():
        with open(log_file, 'r', encoding='utf-8') as f:
            return jsonify(json.load(f))
    return jsonify({"error": "Log not found"}), 404


@app.route('/api/logs/<filename>', methods=['DELETE'])
def api_delete_log(filename):
    """ログファイルを削除"""
    # セキュリティ: ファイル名にパス区切り文字が含まれていないか確認
    if '/' in filename or '\\' in filename or '..' in filename:
        return jsonify({"success": False, "error": "Invalid filename"}), 400
    
    log_file = LOG_DIR / filename
    if log_file.exists():
        try:
            os.remove(log_file)
            return jsonify({"success": True})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500
    return jsonify({"success": False, "error": "Log not found"}), 404


@app.route('/api/logs/clear', methods=['POST'])
def api_clear_all_logs():
    """すべてのログファイルを削除"""
    try:
        deleted = 0
        if LOG_DIR.exists():
            for f in LOG_DIR.glob('*.json'):
                os.remove(f)
                deleted += 1
        return jsonify({"success": True, "deleted": deleted})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/daily-count')
def api_get_daily_count():
    """今日の送信数を取得"""
    config = load_config()
    today = datetime.now().strftime('%Y-%m-%d')
    
    # 日付が変わっていたらリセット
    if config.get('daily_sent_date', '') != today:
        return jsonify({
            "date": today,
            "sent_count": 0,
            "max_count": config.get('max_send_count', 0)
        })
    
    return jsonify({
        "date": config.get('daily_sent_date', today),
        "sent_count": config.get('daily_sent_count', 0),
        "max_count": config.get('max_send_count', 0)
    })


@app.route('/api/daily-count/reset', methods=['POST'])
def api_reset_daily_count():
    """今日の送信数をリセット"""
    config = load_config()
    config['daily_sent_count'] = 0
    save_config(config)
    return jsonify({"success": True})


if __name__ == '__main__':
    LOG_DIR.mkdir(exist_ok=True)
    print("\n" + "="*50)
    print("  SMS送信システム ダッシュボード")
    print("  http://localhost:5000 でアクセス")
    print("="*50 + "\n")
    app.run(host='0.0.0.0', port=5000, debug=True, threaded=True)
