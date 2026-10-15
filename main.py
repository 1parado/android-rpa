# -*- coding: utf-8 -*-
"""入口：python main.py --task boss --dry-run"""
import argparse
import sys

import config
from core.actions import Interrupted, Robot, setup_logging
from core.device import connect

sys.path.insert(0, ".")   # 保证 python main.py 从项目根目录可运行


def main():
    setup_logging()
    ap = argparse.ArgumentParser(description="Android RPA 主控")
    ap.add_argument("--task", required=True,
                    choices=["boss", "wechat"],
                    help="要执行的任务")
    ap.add_argument("--serial", default=None, help="设备序列号（多设备时指定）")
    ap.add_argument("--live", action="store_true",
                    help="真实执行操作（默认 dry-run，只找控件不点击）")
    ap.add_argument("--max", type=int, default=None,
                    help="本轮最多打招呼数量（默认取 config.BOSS.max_greetings）")
    args = ap.parse_args()

    d = connect(args.serial)
    r = Robot(d, dry_run=not args.live)

    try:
        if args.task == "boss":
            from apps import boss_zhipin
            boss_zhipin.run(r, keywords=config.BOSS.get("job_keywords") or None,
                            max_greet=args.max)
        elif args.task == "wechat":
            from apps import wechat
            wechat.run(r)
    except Interrupted:
        log.info("⛔ 任务已被人为主动打断（INTERRUPT）。双击 resume_agent.bat 可恢复。")
    except RuntimeError as e:
        if "打断" in str(e) or "操作上限" in str(e):
            log.info(f"⛔ {e}")
        else:
            raise


if __name__ == "__main__":
    main()
