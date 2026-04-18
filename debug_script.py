#!/usr/bin/env python3
"""
调试脚本 - 快速测试和诊断

用法:
    python debug_script.py              # 运行所有诊断
    python debug_script.py --agent      # 只测试智能体
    python debug_script.py --vrp        # 只测试 VRP
    python debug_script.py --api        # 只测试 API Key
"""

import sys
import os
import json
from datetime import datetime
import asyncio

# 添加路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from loguru import logger

# 配置日志
logger.remove()
logger.add(sys.stdout, level="DEBUG", format="{time:HH:mm:ss} | {level} | {message}")


def check_env():
    """检查环境变量"""
    logger.info("=" * 60)
    logger.info("检查环境配置")
    logger.info("=" * 60)
    
    api_key = os.getenv("GOOGLE_API_KEY", "")
    
    if not api_key:
        logger.error("❌ GOOGLE_API_KEY 未设置")
        logger.info("提示：export GOOGLE_API_KEY='your-key' 或创建 .env 文件")
        return False
    
    if len(api_key) < 30:
        logger.error(f"❌ API Key 长度异常：{len(api_key)} 字符")
        return False
    
    if not api_key.startswith(("AIza", "Alza")):
        logger.warning(f"⚠️ API Key 格式可能不正确：{api_key[:10]}...")
    
    logger.success(f"✅ API Key 已设置 ({len(api_key)} 字符)")
    logger.info(f"   前缀：{api_key[:10]}...")
    return True


async def test_agents():
    """测试多智能体系统"""
    logger.info("=" * 60)
    logger.info("测试多智能体系统")
    logger.info("=" * 60)
    
    try:
        from agents.coordinator import MultiAgentCoordinator
        
        coordinator = MultiAgentCoordinator()
        logger.success("✅ 协调器初始化成功")
        
        # 注册测试供应点
        coordinator.register_supply_agent("TEST001", {"lat": 57.7089, "lon": 14.1618})
        logger.success("✅ 供应智能体注册成功")
        
        # 设置测试数据
        for agent in coordinator.supply_agents.values():
            agent.current_stock = 10.0
            agent.daily_capacity = 20.0
        
        # 获取系统概览
        overview = await coordinator.get_system_overview()
        logger.success(f"✅ 系统概览获取成功")
        logger.info(f"   供应点：{overview['supply_points']}")
        logger.info(f"   车队：{overview['fleet_status']['total_vehicles']} 辆")
        logger.info(f"   需求点：{overview['demand_points']}")
        
        # 运行优化
        result = await coordinator.run_optimization_cycle()
        logger.success(f"✅ 优化运行成功 (ID: {result['optimization_id']})")
        logger.info(f"   匹配数：{result['matches']['total_matches']}")
        logger.info(f"   总吨位：{result['matches']['total_tons']} 吨")
        
        return True
        
    except Exception as e:
        logger.error(f"❌ 智能体测试失败：{e}")
        import traceback
        logger.debug(traceback.format_exc())
        return False


async def test_vrp():
    """测试 VRP 求解器"""
    logger.info("=" * 60)
    logger.info("测试 VRP 求解器")
    logger.info("=" * 60)
    
    try:
        from optimization.vrp_solver import VRPSolver, Location, Vehicle
        
        solver = VRPSolver()
        
        # 添加节点
        depot = Location(id="DEPOT", lat=57.7089, lon=14.1618, type="depot")
        solver.add_location(depot)
        
        for i in range(5):
            solver.add_location(Location(
                id=f"P{i}",
                lat=57.7089 + (i * 0.05),
                lon=14.1618 + (i * 0.05),
                demand_tons=3.0,
                type="pickup"
            ))
        
        # 添加车辆
        for i in range(2):
            solver.add_vehicle(Vehicle(
                id=f"V{i}",
                capacity_tons=15.0,
                start_location=depot
            ))
        
        # 求解
        logger.info("正在求解 VRP...")
        result = solver.solve(time_limit_seconds=10)
        
        if result["status"] in ["optimal", "heuristic"]:
            logger.success(f"✅ VRP 求解成功 ({result['computation_method']})")
            logger.info(f"   总距离：{result['total_distance_km']} km")
            logger.info(f"   总成本：{result['total_cost_sek']} SEK")
            logger.info(f"   碳排放：{result['total_co2_kg']} kg")
            logger.info(f"   使用车辆：{result['num_vehicles_used']} 辆")
        else:
            logger.warning(f"⚠️ VRP 求解结果：{result['status']}")
            logger.info(f"   消息：{result.get('message', 'N/A')}")
        
        return True
        
    except Exception as e:
        logger.error(f"❌ VRP 测试失败：{e}")
        import traceback
        logger.debug(traceback.format_exc())
        return False


def test_api_connection():
    """测试 API 连接"""
    logger.info("=" * 60)
    logger.info("测试 Google API 连接")
    logger.info("=" * 60)
    
    try:
        from google.adk import Agent
        logger.success("✅ Google ADK 导入成功")
        
        # 尝试创建简单 Agent（不实际调用）
        agent = Agent(
            name="test_agent",
            model="gemini-2.0-flash",
            description="Test agent for debugging"
        )
        logger.success("✅ Agent 创建成功")
        
        return True
        
    except Exception as e:
        logger.error(f"❌ API 连接测试失败：{e}")
        import traceback
        logger.debug(traceback.format_exc())
        return False


async def run_full_diagnostic():
    """运行完整诊断"""
    logger.info("\n")
    logger.info("🦞 " + "=" * 58)
    logger.info("   Green Logistics AI - 调试诊断")
    logger.info("=" * 60)
    logger.info(f"时间：{datetime.now().isoformat()}")
    logger.info(f"Python: {sys.version}")
    logger.info(f"路径：{os.getcwd()}")
    logger.info("")
    
    results = {
        "env": check_env(),
        "api": test_api_connection(),
        "agents": await test_agents(),
        "vrp": await test_vrp()
    }
    
    logger.info("\n")
    logger.info("=" * 60)
    logger.info("诊断总结")
    logger.info("=" * 60)
    
    for test, passed in results.items():
        status = "✅ 通过" if passed else "❌ 失败"
        logger.info(f"{test.upper():10} : {status}")
    
    all_passed = all(results.values())
    
    if all_passed:
        logger.success("\n🎉 所有测试通过！系统运行正常！")
    else:
        logger.error("\n⚠️ 部分测试失败，请检查错误信息")
    
    return all_passed


def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Green Logistics AI 调试工具")
    parser.add_argument("--env", action="store_true", help="只检查环境")
    parser.add_argument("--api", action="store_true", help="只测试 API")
    parser.add_argument("--agent", action="store_true", help="只测试智能体")
    parser.add_argument("--vrp", action="store_true", help="只测试 VRP")
    args = parser.parse_args()
    
    # 加载 .env 文件
    from dotenv import load_dotenv
    load_dotenv()
    
    if args.env:
        success = check_env()
    elif args.api:
        success = test_api_connection()
    elif args.agent:
        success = asyncio.run(test_agents())
    elif args.vrp:
        success = asyncio.run(test_vrp())
    else:
        success = asyncio.run(run_full_diagnostic())
    
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
