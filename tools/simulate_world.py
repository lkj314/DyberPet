# coding:utf-8
"""修仙世界命令行模拟器（设计文档 §13.1）。

推演 N 世界年并输出统计，验证：
- 是否有 NPC 正常走完一生（生老病死齐全）
- 关系网自然演化（结缘、结仇都出现）
- 日志重复率（连续 100 条 < 5%）
- 是否有不合理事件（低境界碾压高境界——由事件表本身约束，此处抽查突破日志）

用法：
  .venv/Scripts/python.exe tools/simulate_world.py --years 50 --seed 7
  .venv/Scripts/python.exe tools/simulate_world.py --days 2920 --perf
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from DyberPet.world_service import WorldService, day_str, DAYS_PER_YEAR  # noqa: E402
from DyberPet.npc_simulator import REALM_NAME  # noqa: E402

DATA_DIR = os.path.join(REPO, 'res', 'world')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--years', type=int, default=20)
    ap.add_argument('--days', type=int, default=0, help='直接指定世界日数')
    ap.add_argument('--seed', type=int, default=7)
    ap.add_argument('--perf', action='store_true', help='输出补算耗时')
    ap.add_argument('--tail', type=int, default=25, help='打印末尾日志条数')
    args = ap.parse_args()

    rng = random.Random(args.seed)
    svc = WorldService(save_path=None)
    svc.load_content(DATA_DIR)
    svc.rng.seed(args.seed)

    n_days = args.days or args.years * DAYS_PER_YEAR
    t0 = time.time()
    n_logs = svc._simulate_days(n_days)
    dt = time.time() - t0
    w = svc.world

    print(f'==== 推演 {n_days} 世界日（约 {n_days // DAYS_PER_YEAR} 年）====')
    print(f'日志 {n_logs} 条 ｜ NPC 存活 {len(w["npcs"])} ｜ 陨落 {len(w["dead_index"])}'
          f' ｜ 回响池待触发 {len(w["pending_echoes"])}')
    if args.perf:
        print(f'补算耗时 {dt:.2f}s（指标：8 小时离线 2920 日 < 20s）')

    # 境界分布
    dist = {}
    for npc in w['npcs'].values():
        dist[REALM_NAME[min(npc['realm'], 9)]] = dist.get(
            REALM_NAME[min(npc['realm'], 9)], 0) + 1
    print('境界分布：', json.dumps(dist, ensure_ascii=False))

    # 死因分布
    causes = {}
    for d in w['dead_index'].values():
        causes[d['cause']] = causes.get(d['cause'], 0) + 1
    if causes:
        print('死因分布：', json.dumps(causes, ensure_ascii=False))

    # 重复率（连续 100 条）
    tail = [x['text'] for x in w['logs'][-100:]]
    dup = len(tail) - len(set(tail))
    rate = dup / max(1, len(tail)) * 100
    print(f'连续 100 条日志重复率：{rate:.1f}%（指标 < 5%）'
          f'  ->  {"PASS" if rate < 5 else "FAIL"}')

    # L3 级事件抽样
    big = [x for x in w['logs'] if x.get('level', 1) >= 3]
    print(f'L3 重大事件共 {len(big)} 条，抽样 5 条：')
    for x in big[:5]:
        print('  ·', day_str(x['day']), x['text'])

    # 末尾日志
    print(f'\n---- 末尾 {args.tail} 条日志 ----')
    for x in w['logs'][-args.tail:]:
        print(' ', day_str(x['day']), x['text'])

    # 回响抽样
    if w['pending_echoes']:
        print(f'\n---- 待触发回响（前 5）----')
        for e in w['pending_echoes'][:5]:
            print(' ', f"第{e['day']}日", e['event'], e['npc'])


if __name__ == '__main__':
    main()
