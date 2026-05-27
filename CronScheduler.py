from dataclasses import dataclass
from pathlib import Path
import uuid
import time
import json
from datetime import datetime
from typing import Optional
from queue import Queue


@dataclass
class CronSchedulerConfig:
    work_dir: Path


class CronScheduler:
    def __init__(self, config: CronSchedulerConfig):
        self.config = config
        self.jobs = []
        self.queue = Queue()
        self.load_all()
        # self.check_loop()

    def create_schedule(self, cron_expr: str, prompt: str, recurring: bool = True):
        id = self.new_id()
        file_path = self.config.work_dir / f"{id}.json"
        if not self.config.work_dir.exists():
            self.config.work_dir.mkdir(parents=True, exist_ok=True)
        job = {
            "schedule_id": id,
            "cron": cron_expr,
            "prompt": prompt,
            "recurring": recurring,
            "created_at": time.time(),
            "last_fired_at": None,
        }
        self.jobs.append(job)
        file_path.write_text(json.dumps(job))
        return job

    def get_queue(self) -> Queue:
        return self.queue

    def check_loop(self):
        while True:
            now = datetime.now()
            self.check_jobs(now)
            time.sleep(60)

    def check_jobs(self, now: datetime):
        for job in self.jobs:
            if self.cron_matches(job["cron"], now) and (
                (
                    int(job["last_fired_at"] // 60) != int(now.timestamp() // 60)
                    and job["recurring"]
                )
                or job["last_fired_at"] is None
            ):
                self.queue.put(
                    {
                        "type": "scheduled_prompt",
                        "schedule_id": job["schedule_id"],
                        "prompt": job["prompt"],
                    }
                )
                job["last_fired_at"] = now.timestamp()
                file_path = self.config.work_dir / f"{job['schedule_id']}.json"
                file_path.write_text(json.dumps(job))

    def load_all(self):
        if self.config.work_dir.exists():
            for file_path in self.config.work_dir.glob("*.json"):
                job = json.loads(file_path.read_text())
                self.jobs.append(job)

    def cron_matches(self, cron_expr: str, dt: Optional[datetime] = None) -> bool:
        """
        判断给定的时间是否匹配cron表达式

        cron表达式格式: 分 时 日 月 周
        支持:
            *       任意值
            */N     每N个单位
            N       精确值
            N-M     范围
            N,M     列表

        Args:
            cron_expr: cron表达式，如 "*/5 * * * *" 表示每5分钟
            dt: 要检查的时间，默认为当前时间

        Returns:
            True: 匹配, False: 不匹配
        """
        if dt is None:
            dt = datetime.now()

        fields = cron_expr.strip().split()
        if len(fields) != 5:
            raise ValueError(
                f"cron表达式需要5个字段，当前有{len(fields)}个: {cron_expr}"
            )

        # 获取时间值: [分钟, 小时, 日, 月, 星期几]
        values = [dt.minute, dt.hour, dt.day, dt.month, dt.weekday()]
        # Python星期: 0=周一; cron星期: 0=周日，这里转换为cron标准
        cron_weekday = (dt.weekday() + 1) % 7
        values[4] = cron_weekday

        # 各字段的取值范围
        ranges = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 6)]

        for field, value, (lo, hi) in zip(fields, values, ranges):
            if not self._match_field(field, value, lo, hi):
                return False
        return True

    def _match_field(self, field: str, value: int, lo: int, hi: int) -> bool:
        """匹配单个cron字段"""
        if field == "*":
            return True

        # 处理逗号列表: 1,2,3
        for part in field.split(","):
            # 处理步长: */5 或 1-10/2
            if "/" in part:
                base, step_str = part.split("/", 1)
                step = int(step_str)
                if base == "*":
                    # */N: 每隔N
                    if (value - lo) % step == 0:
                        return True
                elif "-" in base:
                    # a-b/N: 范围内每隔N
                    start, end = map(int, base.split("-"))
                    if start <= value <= end and (value - start) % step == 0:
                        return True
                else:
                    # N/N 这种情况不合理，按精确值处理
                    if int(base) == value:
                        return True
            # 处理范围: 1-5
            elif "-" in part:
                start, end = map(int, part.split("-"))
                if start <= value <= end:
                    return True
            # 处理精确值
            else:
                if int(part) == value:
                    return True

        return False

    @staticmethod
    def new_id() -> str:
        return str(uuid.uuid4())
