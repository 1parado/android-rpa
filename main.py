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
                    choices=["boss", "wechat", "bili", "bili_up"],
                    help="要执行的任务")
    ap.add_argument("--serial", default=None, help="设备序列号（多设备时指定）")
    ap.add_argument("--live", action="store_true",
                    help="真实执行操作（默认 dry-run，只找控件不点击）")
    ap.add_argument("--max", type=int, default=None,
                    help="本轮最多打招呼数量（默认取 config.BOSS.max_greetings）")
    ap.add_argument("--fresh", action="store_true",
                    help="忽略断点续跑状态，从头扫描（boss 任务）")
    ap.add_argument("--screens", type=int, default=None,
                    help="推荐流翻屏次数（bili 任务，默认取 config.BILI）")
    ap.add_argument("--up", default=None,
                    help="bili_up 任务：只采这些 UP（备注名，逗号分隔；默认全采）")
    ap.add_argument("--detail", type=int, default=None,
                    help="bili_up 任务：点进最近 N 条视频采点赞/投币/收藏（默认取 config.BILI.up_detail）")
    ap.add_argument("--up-screens", type=int, default=None, dest="up_screens",
                    help="bili_up 任务：UP 主页视频列表翻屏次数（默认取 config.BILI.up_screens）")
    args = ap.parse_args()

    d = connect(args.serial)
    r = Robot(d, dry_run=not args.live)

    try:
        if args.task == "boss":
            from apps import boss_zhipin
            boss_zhipin.run(r, keywords=config.BOSS.get("job_keywords") or None,
                            max_greet=args.max, fresh=args.fresh)
        elif args.task == "wechat":
            from apps import wechat
            wechat.run(r)
        elif args.task == "bili":
            from apps import bilibili
            bilibili.run(r, screens=args.screens)
        elif args.task == "bili_up":
            from apps import bilibili
            bilibili.run_up(r, up_names=args.up.split(",") if args.up else None,
                            detail=args.detail, screens=args.up_screens)
    except Interrupted:
        log.info("⛔ 任务已被人为主动打断（INTERRUPT）。双击 resume_agent.bat 可恢复。")
    except RuntimeError as e:
        if "打断" in str(e) or "操作上限" in str(e):
            log.info(f"⛔ {e}")
        else:
            raise


if __name__ == "__main__":
    main()
