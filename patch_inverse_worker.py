#!/usr/bin/env python3
"""
patch_inverse_worker.py
───────────────────────
manager.py 에 InverseWorker 추가
- /i 명령으로 인버스봇 제어
- inverse_cfg.yaml 설정
"""
import os, sys, shutil, py_compile
from datetime import datetime

BASE_DIR     = "/home/trade/upbit_bot"
MANAGER_FILE = os.path.join(BASE_DIR, "manager.py")
BACKUP_DIR   = os.path.join(BASE_DIR, "backups")
os.makedirs(BACKUP_DIR, exist_ok=True)

def backup(path):
    ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = os.path.join(BACKUP_DIR, os.path.basename(path) + f".bak_{ts}")
    shutil.copy2(path, dst)
    print(f"  백업: {dst}")

def check(path):
    try:
        py_compile.compile(path, doraise=True)
        print("  ✅ 문법 OK"); return True
    except py_compile.PyCompileError as e:
        print(f"  ❌ {e}"); return False

def restore(path):
    baks = sorted([x for x in os.listdir(BACKUP_DIR) if os.path.basename(path) in x])
    if baks:
        shutil.copy2(os.path.join(BACKUP_DIR, baks[-1]), path)
        print(f"  복원: {baks[-1]}")

# ── InverseWorker 클래스 ──────────────────────────────────────
INVERSE_WORKER = '''
# ============================================================
# [PATCH] InverseWorker — 나스닥→인버스 봇
# ============================================================
class InverseWorker:
    """inverse_bot.py 를 subprocess로 실행하는 워커."""

    def __init__(self, script="inverse_bot.py"):
        self.script     = os.path.join(BASE_DIR, script)
        self.worker_id  = "INVERSE"
        self.process    = None
        self.thread     = None
        self._stop_event = threading.Event()

    def _run(self):
        cfg_file = os.path.join(BASE_DIR, "inverse_cfg.yaml")
        cmd = [sys.executable, self.script]
        if os.path.exists(cfg_file):
            cmd += ["--config", cfg_file]
        while not self._stop_event.is_set():
            try:
                cprint(f"  [인버스봇] 시작: {' '.join(cmd)}", Fore.CYAN)
                self.process = subprocess.Popen(
                    cmd, cwd=BASE_DIR,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, bufsize=1
                )
                for line in self.process.stdout:
                    line = line.rstrip()
                    if line:
                        print(f"  [인버스봇] {line}")
                self.process.wait()
                if self._stop_event.is_set():
                    break
                cprint("  [인버스봇] 비정상 종료 — 5초 후 재시작", Fore.YELLOW)
                time.sleep(5)
            except Exception as e:
                cprint(f"  [인버스봇 오류] {e}", Fore.RED)
                time.sleep(5)

    def start(self):
        self._stop_event.clear()
        self.thread = threading.Thread(target=self._run, daemon=True, name="worker-inverse")
        self.thread.start()
        cprint("✅ [인버스봇] 워커 시작", Fore.GREEN)

    def stop(self):
        self._stop_event.set()
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except Exception:
                self.process.kill()
                cprint("⏹ [인버스봇] 강제 종료", Fore.RED)

'''

# ── /i 명령 핸들러 ────────────────────────────────────────────
INVERSE_CMD = '''
    # ── /i → 인버스봇 명령 전달 ─────────────────────────────
    elif cmd[0] == "/i":
        sub = " ".join(cmd[1:]) if len(cmd) > 1 else "status"
        import uuid as _uuid
        req_id  = _uuid.uuid4().hex[:8]
        sub_cmd = "/" + sub
        cmd_file    = os.path.join(SHARED_DIR, "cmd_inverse.json")
        result_file = os.path.join(SHARED_DIR, "result_inverse.json")
        # 인버스봇 실행 중인지 확인
        inv_workers = [w for w in list(_workers) if isinstance(w, InverseWorker)]
        if not inv_workers:
            send_msg("📉 인버스봇 실행 안 됨", level="normal", source="매니저", force=True)
            return
        # IPC 전송
        try:
            tmp = cmd_file + ".tmp"
            import json as _json
            with open(tmp, "w", encoding="utf-8") as f:
                _json.dump({"cmd": sub_cmd, "req_id": req_id, "ts": time.time()}, f)
            os.replace(tmp, cmd_file)
        except Exception as e:
            cprint(f"[인버스 IPC 오류] {e}", Fore.YELLOW)
            return
        # 결과 수신
        deadline = time.time() + 8.0
        while time.time() < deadline:
            if os.path.exists(result_file):
                try:
                    with open(result_file, encoding="utf-8") as f:
                        data = _json.load(f)
                    os.remove(result_file)
                    result = data.get("result","")
                    clean  = result.replace("[critical] ","").replace("[normal] ","").replace("[silent] ","")
                    if clean.strip():
                        send_msg(clean, level="normal", source="📉인버스", force=True)
                    return
                except Exception:
                    pass
            time.sleep(0.2)
        send_msg("⚠️ 인버스봇 응답 없음", level="normal", source="매니저", force=True)

'''

# ── 인버스봇 result 폴링 추가 (_poll_ipc_results) ────────────
OLD_POLL = '    for filename, source, kb in targets:'
NEW_POLL = (
    '    # 인버스봇 result 폴링\n'
    '    inv_result = os.path.join(SHARED_DIR, "result_inverse.json")\n'
    '    if os.path.exists(inv_result):\n'
    '        try:\n'
    '            with _ipc_lock(inv_result):\n'
    '                with open(inv_result, encoding="utf-8") as f:\n'
    '                    _idata = json.load(f)\n'
    '                os.remove(inv_result)\n'
    '            _itext = _idata.get("result","")\n'
    '            _ilevel = "critical" if "[critical]" in _itext else "normal"\n'
    '            _iclean = _itext.replace("[critical] ","").replace("[normal] ","").replace("[silent] ","")\n'
    '            if _iclean.strip() and _ilevel != "silent":\n'
    '                send_msg(_iclean, level=_ilevel, source="📉인버스", force=True)\n'
    '        except Exception:\n'
    '            pass\n'
    '    for filename, source, kb in targets:'
)

# ── run_manager() 에서 InverseWorker 시작 ────────────────────
OLD_RUN = '    if not _workers:\n        cprint("❌ 실행할 봇이 없어요.'
NEW_RUN = (
    '    # 인버스봇 워커\n'
    '    inv_cfg_file = os.path.join(BASE_DIR, "inverse_cfg.yaml")\n'
    '    inv_script   = os.path.join(BASE_DIR, "inverse_bot.py")\n'
    '    if os.path.exists(inv_script):\n'
    '        inv_w = InverseWorker(script="inverse_bot.py")\n'
    '        _workers.append(inv_w)\n'
    '        cprint("✅ [인버스봇] 워커 등록", Fore.CYAN)\n'
    '    else:\n'
    '        cprint("⚠️ inverse_bot.py 없음 — 인버스봇 건너뜀", Fore.YELLOW)\n'
    '    if not _workers:\n'
    '        cprint("❌ 실행할 봇이 없어요.'
)

def patch(src):
    if "InverseWorker" in src:
        print("  ⏭ 이미 패치됨"); return src

    # 1) InverseWorker 클래스 삽입 (StockWorker 클래스 뒤)
    anchor1 = "# ============================================================\n# [8] 텔레그램 명령 처리"
    if anchor1 in src:
        src = src.replace(anchor1, INVERSE_WORKER + anchor1, 1)
        print("  ✅ InverseWorker 클래스 삽입")
    else:
        print("  ❌ InverseWorker 삽입 위치 못 찾음")

    # 2) /i 명령 핸들러 삽입 (/s 핸들러 뒤)
    anchor2 = '    elif cmd[0] == "/set" and len(cmd) >= 3:'
    if anchor2 in src:
        src = src.replace(anchor2, INVERSE_CMD + anchor2, 1)
        print("  ✅ /i 핸들러 삽입")
    else:
        print("  ❌ /i 핸들러 삽입 위치 못 찾음")

    # 3) result 폴링 추가
    if OLD_POLL in src:
        src = src.replace(OLD_POLL, NEW_POLL, 1)
        print("  ✅ 인버스봇 result 폴링 추가")
    else:
        print("  ⚠️  result 폴링 위치 못 찾음")

    # 4) run_manager에서 InverseWorker 시작
    if OLD_RUN in src:
        src = src.replace(OLD_RUN, NEW_RUN, 1)
        print("  ✅ run_manager InverseWorker 시작 추가")
    else:
        print("  ⚠️  run_manager 삽입 위치 못 찾음")

    return src

print("=" * 50)
print("patch_inverse_worker.py")
print("=" * 50)

backup(MANAGER_FILE)
with open(MANAGER_FILE, encoding="utf-8") as f:
    src = f.read()
src = patch(src)
with open(MANAGER_FILE, "w", encoding="utf-8") as f:
    f.write(src)
if not check(MANAGER_FILE):
    restore(MANAGER_FILE); sys.exit(1)

print("\n완료! 적용:")
print("  # inverse_bot.py, inverse_cfg.yaml 서버에 복사 후")
print("  git add manager.py inverse_bot.py patch_inverse_worker.py")
print("  git commit -m 'feat: inverse bot'")
print("  git push && 텔레그램 /update")
print()
print("명령어: /i status  /i signal  /i sell  /i buy  /i set tp 1.5")
print("=" * 50)
