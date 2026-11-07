# -*- coding: utf-8 -*-
"""B-6 数据安全加固：备份 / 恢复 / 备份监控 / 访问日志。

任务卡 B-6 口径（2026-11 分工）：
  - 备份对象：memory_bank.json / conversations.json / memory_events.json /
    emotion_log.json / reminders.json / 用户画像.txt（空间图谱文件到位后加入清单）
  - 本地+异地：本地 backups/ 每文件保留最近 7 份；异地（加密云私有桶，团队付费；
    无云账号则移动硬盘交指定亲属每周更新）由 remote_target 配置，未配置 → health 报 warn
  - 备份监控（静默失败=没备份）：上次成功备份 >24h → alert，并入 B-4 每日自检
    （memory_service.health 合并输出），B-10 报告收"备份健康"项
  - 访问日志：backup/restore/delete/export 全记录（谁在什么时候动过数据，可审计）
  - 原子写：生产文件已有原子写（app.save_json_safe / MemoryBank._save）；
    恢复同样先写临时文件再 os.replace，防恢复拿到写一半的文件
  - 数据目录文件权限（仅主系统账号可访问）：Windows ACL 操作规程见 B-6 文档 SOP，
    代码层以访问日志兜底审计

设计约束：本模块零第三方依赖、不 import app（可独立单测/被 backend service 复用）。
"""
import json
import os
import shutil
import tempfile
import threading
import time

DEFAULT_BACKUP_DIR = "backups"
DEFAULT_KEEP = 7                       # B-6 写死：保留最近 7 份
BACKUP_STALE_HOURS = 24                # B-6 写死：上次成功 >24h = 告警（静默失败=没备份）
DEFAULT_BACKUP_FILES = [
    "memory_bank.json", "conversations.json", "memory_events.json",
    "emotion_log.json", "reminders.json", "用户画像.txt",
    "emergency_contact.json",   # B-11 紧急联系人（安全数据，必须可恢复）
]


class BackupManager:
    """数据文件备份/恢复管理器（B-6）。一个进程一个实例即可，内部有锁。"""

    def __init__(self, data_dir=".", backup_dir=DEFAULT_BACKUP_DIR,
                 remote_target="", keep=DEFAULT_KEEP, now=None):
        self.data_dir = str(data_dir or ".")
        self.backup_dir = str(backup_dir or DEFAULT_BACKUP_DIR)
        self.remote_target = str(remote_target or "").strip()
        self.keep = max(1, int(keep))
        self._clock = now or time.time
        self._lock = threading.Lock()
        os.makedirs(self.backup_dir, exist_ok=True)
        self.state_path = os.path.join(self.backup_dir, "backup_state.json")
        self.access_log_path = os.path.join(self.backup_dir, "access_log.jsonl")

    # ---------------- 状态 ----------------
    def _load_state(self):
        try:
            with open(self.state_path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"last_success_ts": 0, "files": {}, "errors": []}

    def _save_state(self, state):
        self._atomic_json(self.state_path, state)

    @staticmethod
    def _atomic_json(path, data):
        """原子写 JSON：先写临时文件再替换（与生产文件同一套纪律）。"""
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".",
                                   prefix=".bk_", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except Exception:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise

    def log_access(self, action, detail="", now=None):
        """访问日志（B-6 验收项：谁在什么时候动过数据，可审计）。
        action ∈ backup|restore|delete|export|config。append-only JSONL。"""
        entry = {"ts": now or self._clock(), "action": str(action or ""),
                 "detail": str(detail or ""), "pid": os.getpid()}
        with self._lock:
            try:
                with open(self.access_log_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            except OSError:
                pass
        return entry

    # ---------------- 备份 ----------------
    def _backups_of(self, file_path):
        base = os.path.basename(file_path)
        try:
            names = [n for n in os.listdir(self.backup_dir)
                     if n.startswith(base + ".") and n.endswith(".bak")]
        except OSError:
            return []
        return sorted(names)   # 时间戳前缀命名，字典序即时间序

    def backup_file(self, file_path, now=None):
        """备份单个文件到本地备份目录，滚动保留最近 keep 份。
        返回 {"ok", "backup", "kept"}；文件不存在 → ok=False（不算失败事件）。"""
        now = now or self._clock()
        file_path = os.path.join(self.data_dir, file_path) \
            if not os.path.isabs(file_path) else file_path
        if not os.path.exists(file_path):
            return {"ok": False, "reason": "not_found"}
        with self._lock:
            state = self._load_state()
            try:
                ts = time.strftime("%Y%m%d_%H%M%S", time.localtime(now))
                dst = os.path.join(self.backup_dir,
                                   f"{os.path.basename(file_path)}.{ts}.bak")
                shutil.copy2(file_path, dst)
                kept = self._backups_of(file_path)
                removed = 0
                while len(kept) > self.keep:
                    os.remove(os.path.join(self.backup_dir, kept.pop(0)))
                    removed += 1
                state["last_success_ts"] = now
                state["last_success"] = os.path.basename(file_path)
                state.setdefault("files", {})[os.path.basename(file_path)] = now
                state["errors"] = (state.get("errors") or [])[-9:]
                self._save_state(state)
            except OSError as e:
                state["errors"] = (state.get("errors") or [])[-9:] + \
                    [{"ts": now, "error": str(e)}]
                self._save_state(state)
                return {"ok": False, "reason": str(e)}
        self.log_access("backup", os.path.basename(file_path), now=now)
        return {"ok": True, "backup": os.path.basename(dst), "kept": self.keep}

    def backup_all(self, files=None, now=None):
        """按 B-6 清单全量备份。返回 {"ok", "backed", "missing"}。"""
        files = list(files or DEFAULT_BACKUP_FILES)
        now = now or self._clock()
        backed, missing = [], []
        for name in files:
            res = self.backup_file(name, now=now)
            (backed if res.get("ok") else missing).append(name)
        return {"ok": True, "backed": backed, "missing": missing}

    # ---------------- 恢复 ----------------
    @staticmethod
    def _read_restorable(path):
        """校验备份文件完好（JSON 能解析；文本能读）才允许恢复——坏备份不覆盖好数据。"""
        if path.endswith(".json.bak") or ".json." in os.path.basename(path):
            with open(path, encoding="utf-8") as f:
                json.load(f)     # 解析失败即损坏，抛异常拒绝恢复
            with open(path, encoding="utf-8") as f:
                return f.read()
        with open(path, encoding="utf-8") as f:
            return f.read()

    def restore(self, file_name, timestamp=None, now=None):
        """恢复文件（B-6 恢复演练走这里）：默认取最新一份 .bak，原子替换回原位。
        返回 {"ok", "restored", "source"}；损坏的备份拒绝恢复（ok=False）。"""
        now = now or self._clock()
        with self._lock:
            cands = self._backups_of(file_name)
            if not cands:
                return {"ok": False, "reason": "no_backup"}
            if timestamp:
                cands = [n for n in cands if timestamp in n]
                if not cands:
                    return {"ok": False, "reason": "backup_not_found"}
            src_name = cands[-1]
            src = os.path.join(self.backup_dir, src_name)
            try:
                content = self._read_restorable(src)
            except Exception as e:
                return {"ok": False, "reason": f"backup_corrupt: {e}"}
            dst = os.path.join(self.data_dir, file_name)
            fd, tmp = tempfile.mkstemp(dir=self.data_dir, prefix=".restore_")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(content)
                os.replace(tmp, dst)
            except Exception:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                raise
        self.log_access("restore", f"{file_name} <- {src_name}", now=now)
        return {"ok": True, "restored": file_name, "source": src_name}

    # ---------------- 备份监控（静默失败 = 没备份）----------------
    def health(self, now=None):
        """备份健康（并入 B-4 每日自检，B-10 报告"备份健康"项）：
        - alert：上次成功备份 >24h 或从未备份（静默失败=没备份，必须告警）
        - warn：无异地备份配置（SOP：移动硬盘交指定亲属每周更新）
        - ok：24h 内有成功备份
        """
        now = now or self._clock()
        with self._lock:
            state = self._load_state()
        last = float(state.get("last_success_ts") or 0)
        hours = (now - last) / 3600.0 if last else None
        issues = []
        status = "ok"
        if not last:
            status, issues = "alert", ["never_backed_up"]
        elif hours > BACKUP_STALE_HOURS:
            status, issues = "alert", ["backup_stale_over_24h"]
        if not self.remote_target:
            issues.append("no_offsite_target")
            if status == "ok":
                status = "warn"
        return {"status": status, "issues": issues,
                "last_success_ts": last,
                "hours_since_last": round(hours, 1) if hours is not None else None,
                "has_offsite": bool(self.remote_target),
                "offsite_target": self.remote_target or None,
                "keep": self.keep, "recent_errors": state.get("errors") or []}


_default_manager = None
_default_lock = threading.Lock()


def default_manager(data_dir=".", backup_dir=DEFAULT_BACKUP_DIR, remote_target=None):
    """进程级默认实例（app.py / memory_service 共用，路径与生产一致）。
    remote_target 优先读环境变量 XIAOYI_BACKUP_REMOTE（B-6 文档 §备份 SOP 配置位）。"""
    global _default_manager
    with _default_lock:
        if _default_manager is None:
            remote = remote_target if remote_target is not None \
                else os.environ.get("XIAOYI_BACKUP_REMOTE", "")
            _default_manager = BackupManager(data_dir=data_dir,
                                             backup_dir=backup_dir,
                                             remote_target=remote)
        return _default_manager


__all__ = ["BackupManager", "DEFAULT_BACKUP_FILES", "DEFAULT_BACKUP_DIR",
           "DEFAULT_KEEP", "BACKUP_STALE_HOURS", "default_manager"]
