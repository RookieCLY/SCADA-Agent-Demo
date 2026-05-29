import json
from pathlib import Path
from eval.schema import GoldenRecord, ExpectedFinalStateDiff, ExpectedTrajectory

def generate():
    records = []
    
    # 1. simple, page, clear, standard, success, free (no wf)
    records.append(GoldenRecord(
        id="golden-001",
        query="创建主监控页面，分辨率1920x1080，背景色#000000",
        domain="page",
        complexity="simple",
        initial_world={},
        expected_behavior="success",
        expected_final_state_diff=ExpectedFinalStateDiff(
            match_mode="subset",
            added_or_modified={"pages.main_page.resolution": [1920, 1080], "pages.main_page.background": "#000000"}
        ),
        expected_trajectory=ExpectedTrajectory(min_steps=1, max_steps=2, required_tools=["manage_pages"], required_actions=["create_page"]),
        rubric_hints=["创建page, ID可以由模型自行决定, 确保参数正确"]
    ))

    # 2. simple, point, clear, standard, success, free
    records.append(GoldenRecord(
        id="golden-002",
        query="新增模拟量点位PT101，单位MPa，量程0-2.5",
        domain="point",
        complexity="simple",
        initial_world={},
        expected_behavior="success",
        expected_final_state_diff=ExpectedFinalStateDiff(
            match_mode="subset",
            added_or_modified={"points.PT101.type": "analog", "points.PT101.unit": "MPa", "points.PT101.min": 0.0, "points.PT101.max": 2.5}
        ),
        expected_trajectory=ExpectedTrajectory(min_steps=1, max_steps=2, required_tools=["manage_points"], required_actions=["create_point"]),
    ))

    # 3. simple, alarm, clear, standard, success, wf: alarm_config
    records.append(GoldenRecord(
        id="golden-003",
        query="给PT101配置高限报警，阈值2.0，中等优先级",
        domain="alarm",
        complexity="simple",
        initial_world={"points": {"PT101": {"tag": "PT101", "type": "analog"}}},
        expected_behavior="success",
        expected_final_state_diff=ExpectedFinalStateDiff(
            match_mode="subset",
            added_or_modified={"alarms.alarm_PT101_hi.tag": "PT101", "alarms.alarm_PT101_hi.high_limit": 2.0, "alarms.alarm_PT101_hi.priority": "medium"}
        ),
        expected_trajectory=ExpectedTrajectory(min_steps=1, max_steps=3, required_tools=["manage_alarms"], required_actions=["create_analog_alarm"]),
        expected_workflow_id="alarm_config"
    ))

    # 4. simple, graphics, clear, standard, success, wf: graphics_layout
    records.append(GoldenRecord(
        id="golden-004",
        query="在主页面上添加一个文本组件，显示'欢迎'",
        domain="graphics",
        complexity="simple",
        initial_world={"pages": {"main_page": {"id": "main_page", "name": "主页面"}}},
        expected_behavior="success",
        expected_final_state_diff=ExpectedFinalStateDiff(
            match_mode="subset",
            added_or_modified={"pages.main_page.widgets.w_text1.type": "text"}
        ),
        expected_workflow_id="graphics_layout"
    ))

    # 5. simple, history, clear, standard, success, wf: history_query
    records.append(GoldenRecord(
        id="golden-005",
        query="对PT101开启历史记录，变化存储模式",
        domain="history",
        complexity="simple",
        initial_world={"points": {"PT101": {"tag": "PT101", "type": "analog"}}},
        expected_behavior="success",
        expected_final_state_diff=ExpectedFinalStateDiff(
            match_mode="subset",
            added_or_modified={"history_configs.PT101.storage_mode": "on_change"}
        ),
        expected_workflow_id="history_query"
    ))

    # 6. simple, script, clear, standard, success, free
    records.append(GoldenRecord(
        id="golden-006",
        query="写个脚本，当PT101变化时执行更新",
        domain="script",
        complexity="simple",
        initial_world={"points": {"PT101": {"tag": "PT101", "type": "analog"}}},
        expected_behavior="success",
        expected_final_state_diff=ExpectedFinalStateDiff(
            match_mode="subset",
            added_or_modified={"scripts.script_pt101.trigger": "on_change", "scripts.script_pt101.bound_tag": "PT101"}
        )
    ))

    # 7. simple, multi, clear, standard, success, free
    records.append(GoldenRecord(
        id="golden-007",
        query="把主监控页面的背景色改成白色，然后重新校验项目",
        domain="multi",
        complexity="simple",
        initial_world={"pages": {"main_page": {"id": "main_page", "name": "主页面", "background": "#000000"}}},
        expected_behavior="success",
        expected_final_state_diff=ExpectedFinalStateDiff(
            match_mode="subset",
            added_or_modified={"pages.main_page.background": "#FFFFFF", "deployments.default.status": "validated"}
        )
    ))

    # 8. simple, page, vague, colloquial, ask_for_clarification, free
    records.append(GoldenRecord(
        id="golden-008",
        query="帮忙建个页面",
        domain="page",
        complexity="simple",
        initial_world={},
        expected_behavior="ask_for_clarification",
        expected_final_state_diff=ExpectedFinalStateDiff(match_mode="strict", added_or_modified={}, removed=[]),
        expected_alternative="追问页面名称、分辨率等基本信息"
    ))

    # 9. simple, point, vague, colloquial, success, wf: point_creation
    records.append(GoldenRecord(
        id="golden-009",
        query="搞个压力点位，名字叫Press_1",
        domain="point",
        complexity="simple",
        initial_world={},
        expected_behavior="success",
        expected_final_state_diff=ExpectedFinalStateDiff(match_mode="subset", added_or_modified={"points.Press_1.type": "analog"}),
        expected_workflow_id="point_creation"
    ))

    # 10. simple, alarm, vague, colloquial, ask_for_clarification, free
    records.append(GoldenRecord(
        id="golden-010",
        query="给那几个温度加上报警",
        domain="alarm",
        complexity="simple",
        initial_world={},
        expected_behavior="ask_for_clarification",
        expected_final_state_diff=ExpectedFinalStateDiff(match_mode="strict"),
        expected_alternative="追问具体哪些温度点位以及报警阈值"
    ))

    # 11. simple, graphics, false premise, jargon, reject, free
    records.append(GoldenRecord(
        id="golden-011",
        query="把主画面的泵1绑到马达3上",
        domain="graphics",
        complexity="simple",
        initial_world={"pages": {"main_page": {"id": "main_page", "name": "主画面", "widgets": {}}}},
        expected_behavior="reject",
        expected_final_state_diff=ExpectedFinalStateDiff(match_mode="strict"),
        expected_error_code="WIDGET_NOT_FOUND",
        expected_alternative="由于页面里没这个图元，应拒绝并报错"
    ))

    # 12. simple, history, false premise, jargon, fail_or_clarify, free
    records.append(GoldenRecord(
        id="golden-012",
        query="查一下冷凝器出口的历史trend，拉个表",
        domain="history",
        complexity="simple",
        initial_world={},
        expected_behavior="fail_or_clarify",
        expected_final_state_diff=ExpectedFinalStateDiff(match_mode="strict"),
        expected_error_code="POINT_NOT_FOUND"
    ))

    # 13. medium, page, multi-step, colloquial, success, free
    records.append(GoldenRecord(
        id="golden-013",
        query="建两个页面，一个叫报警汇总，一个叫报表，都用黑底，顺便把报表页大小设成4K",
        domain="page",
        complexity="medium",
        initial_world={},
        expected_behavior="success",
        expected_final_state_diff=ExpectedFinalStateDiff(
            match_mode="subset",
            added_or_modified={
                "pages.alarm_sum.name": "报警汇总",
                "pages.alarm_sum.background": "#000000",
                "pages.report.name": "报表",
                "pages.report.background": "#000000",
                "pages.report.resolution": [3840, 2160]
            }
        )
    ))

    # 14. medium, point, multi-step, jargon, success, wf: point_creation
    records.append(GoldenRecord(
        id="golden-014",
        query="加三个IO点: TI-201, PI-201, FI-201, 全是AI，带历史",
        domain="point",
        complexity="medium",
        initial_world={},
        expected_behavior="success",
        expected_final_state_diff=ExpectedFinalStateDiff(
            match_mode="subset",
            added_or_modified={
                "points.TI-201.type": "analog",
                "points.PI-201.type": "analog",
                "points.FI-201.type": "analog",
                "history_configs.TI-201.enabled": True,
                "history_configs.PI-201.enabled": True,
                "history_configs.FI-201.enabled": True,
            }
        ),
        expected_workflow_id="point_creation"
    ))

    # 15. medium, alarm, multi-step, Chinglish, success, wf: alarm_config
    records.append(GoldenRecord(
        id="golden-015",
        query="给TI-201 set一个HH alarm和LL alarm，分别在100和10，priority设为high",
        domain="alarm",
        complexity="medium",
        initial_world={"points": {"TI-201": {"tag": "TI-201", "type": "analog"}}},
        expected_behavior="success",
        expected_final_state_diff=ExpectedFinalStateDiff(
            match_mode="subset",
            added_or_modified={
                "alarms.TI-201_HH.high_limit": 100.0,
                "alarms.TI-201_HH.priority": "high",
                "alarms.TI-201_LL.low_limit": 10.0,
                "alarms.TI-201_LL.priority": "high"
            }
        ),
        expected_workflow_id="alarm_config"
    ))

    # 16. medium, graphics, clear, Chinglish, success, wf: chemical_screen
    records.append(GoldenRecord(
        id="golden-016",
        query="在chemical screen加个tank，绑到TI-201上，另外加个button用于start",
        domain="graphics",
        complexity="medium",
        initial_world={
            "pages": {"chem_screen": {"id": "chem_screen", "name": "chemical screen", "widgets": {}}},
            "points": {"TI-201": {"tag": "TI-201", "type": "analog"}, "CMD_START": {"tag": "CMD_START", "type": "digital"}}
        },
        expected_behavior="success",
        expected_final_state_diff=ExpectedFinalStateDiff(
            match_mode="subset",
            added_or_modified={
                "pages.chem_screen.widgets.tank1.type": "tank",
                "pages.chem_screen.widgets.tank1.bindings.level": "TI-201",
                "pages.chem_screen.widgets.btn1.type": "button"
            }
        ),
        expected_workflow_id="chemical_screen"
    ))

    # 17. medium, history, multi-step, colloquial, reject, free
    records.append(GoldenRecord(
        id="golden-017",
        query="帮我把全厂所有的点都加上历史，然后导出来",
        domain="history",
        complexity="medium",
        initial_world={"points": {"P1": {"tag": "P1", "type": "analog"}, "P2": {"tag": "P2", "type": "digital"}}},
        expected_behavior="reject",
        expected_final_state_diff=ExpectedFinalStateDiff(match_mode="strict"),
        expected_error_code="TOO_BROAD",
        expected_alternative="操作影响太大，应拒绝批量盲目操作"
    ))

    # 18. medium, script, clear, jargon, success, free
    records.append(GoldenRecord(
        id="golden-018",
        query="新建全局脚本，OnEvent触发，写一段逻辑如果PI-201过高则把valve1置位",
        domain="script",
        complexity="medium",
        initial_world={"points": {"PI-201": {"tag": "PI-201", "type": "analog"}, "valve1": {"tag": "valve1", "type": "digital"}}},
        expected_behavior="success",
        expected_final_state_diff=ExpectedFinalStateDiff(
            match_mode="subset",
            added_or_modified={"scripts.safety_script.trigger": "on_event"}
        )
    ))

    # 19. medium, multi, clear, standard, success, wf: pump_station_screen
    records.append(GoldenRecord(
        id="golden-019",
        query="新建泵站画面，添加2个泵图元，分别绑定PumpA和PumpB点位",
        domain="multi",
        complexity="medium",
        initial_world={"points": {"PumpA": {"tag": "PumpA", "type": "digital"}, "PumpB": {"tag": "PumpB", "type": "digital"}}},
        expected_behavior="success",
        expected_final_state_diff=ExpectedFinalStateDiff(
            match_mode="subset",
            added_or_modified={
                "pages.pump_station.name": "泵站画面",
                "pages.pump_station.widgets.pump1.type": "pump",
                "pages.pump_station.widgets.pump1.bindings.state": "PumpA",
                "pages.pump_station.widgets.pump2.type": "pump",
                "pages.pump_station.widgets.pump2.bindings.state": "PumpB"
            }
        ),
        expected_workflow_id="pump_station_screen"
    ))

    # 20. medium, page, vague, colloquial, success, free
    records.append(GoldenRecord(
        id="golden-020",
        query="给我复制一份主画面，改名叫备用画面",
        domain="page",
        complexity="medium",
        initial_world={"pages": {"main": {"id": "main", "name": "主画面", "resolution": [1920, 1080], "widgets": {"w1": {"id": "w1", "page_id": "main", "type": "text", "position": [0,0], "size": [10,10]}}}}},
        expected_behavior="success",
        expected_final_state_diff=ExpectedFinalStateDiff(
            match_mode="subset",
            added_or_modified={"pages.backup.name": "备用画面", "pages.backup.resolution": [1920, 1080]}
        )
    ))

    # 21. medium, point, clear, Chinglish, reject, free
    records.append(GoldenRecord(
        id="golden-021",
        query="Delete那个重要的安全联锁点位 Safety_1",
        domain="point",
        complexity="medium",
        initial_world={"points": {"Safety_1": {"tag": "Safety_1", "type": "digital"}}},
        expected_behavior="reject",
        expected_final_state_diff=ExpectedFinalStateDiff(match_mode="strict"),
        expected_alternative="出于安全考虑不应直接删除联锁点，应拒绝"
    ))

    # 22. medium, alarm, multi-step, standard, success, free
    records.append(GoldenRecord(
        id="golden-022",
        query="把所有带Temp的温度点的报警优先级都提高到high",
        domain="alarm",
        complexity="medium",
        initial_world={
            "points": {"Temp_A": {"tag": "Temp_A", "type": "analog"}, "Temp_B": {"tag": "Temp_B", "type": "analog"}},
            "alarms": {
                "al_a": {"id": "al_a", "tag": "Temp_A", "type": "analog", "priority": "medium"},
                "al_b": {"id": "al_b", "tag": "Temp_B", "type": "analog", "priority": "medium"}
            }
        },
        expected_behavior="success",
        expected_final_state_diff=ExpectedFinalStateDiff(
            match_mode="subset",
            added_or_modified={"alarms.al_a.priority": "high", "alarms.al_b.priority": "high"}
        )
    ))

    # 23. medium, graphics, false premise, colloquial, reject, free
    records.append(GoldenRecord(
        id="golden-023",
        query="把图元从页面1拖到页面2，顺便放个背景图",
        domain="graphics",
        complexity="medium",
        initial_world={"pages": {"p1": {"id": "p1", "name": "页面1", "widgets": {}}}},
        expected_behavior="reject",
        expected_final_state_diff=ExpectedFinalStateDiff(match_mode="strict"),
        expected_error_code="PAGE_NOT_FOUND",
        expected_alternative="页面2不存在，应拒绝"
    ))

    # 24. medium, multi, clear, jargon, ask_for_clarification, free
    records.append(GoldenRecord(
        id="golden-024",
        query="建一个trend page，加个trend chart绑定历史数据",
        domain="multi",
        complexity="medium",
        initial_world={},
        expected_behavior="ask_for_clarification",
        expected_final_state_diff=ExpectedFinalStateDiff(match_mode="strict"),
        expected_alternative="应追问需要绑定哪些具体的历史点位"
    ))

    # 25. complex, multi, multi-step, standard, success, wf: chemical_screen
    records.append(GoldenRecord(
        id="golden-025",
        query="建一个反应釜综合监控方案：新建画面，建三个点位（温度、压力、液位），绑到画面的罐子和仪表上，最后下发验证",
        domain="multi",
        complexity="complex",
        initial_world={},
        expected_behavior="success",
        expected_final_state_diff=ExpectedFinalStateDiff(
            match_mode="subset",
            added_or_modified={
                "points.Temp.type": "analog",
                "points.Press.type": "analog",
                "points.Level.type": "analog",
                "pages.chem.widgets.tank.type": "tank",
                "deployments.default.status": "validated"
            }
        ),
        expected_workflow_id="chemical_screen"
    ))

    # 26. complex, multi, vague, colloquial, success, wf: pump_station_screen
    records.append(GoldenRecord(
        id="golden-026",
        query="弄个泵站的控制台，带上启停控制，全套搞定，再弄个报警",
        domain="multi",
        complexity="complex",
        initial_world={},
        expected_behavior="success",
        expected_final_state_diff=ExpectedFinalStateDiff(
            match_mode="subset",
            added_or_modified={
                "pages.pump_ctrl.name": "泵站控制台",
                "points.Pump_Cmd.type": "digital"
            }
        ),
        expected_workflow_id="pump_station_screen"
    ))

    # 27. complex, point, multi-step, jargon, success, free
    records.append(GoldenRecord(
        id="golden-027",
        query="批量生成PT-100到PT-110这11个点，全配上H和HH报警，并且统统挂到历史库里存储模式设为周期，1秒一次",
        domain="point",
        complexity="complex",
        initial_world={},
        expected_behavior="success",
        expected_final_state_diff=ExpectedFinalStateDiff(
            match_mode="subset",
            added_or_modified={
                "points.PT-100.type": "analog",
                "alarms.PT-100_H.priority": "medium",
                "history_configs.PT-100.sample_interval_s": 1.0,
                "points.PT-110.type": "analog"
            }
        )
    ))

    # 28. complex, multi, clear, Chinglish, reject, free
    records.append(GoldenRecord(
        id="golden-028",
        query="Delete整个project的全部pages，然后deploy",
        domain="multi",
        complexity="complex",
        initial_world={"pages": {"p1": {"id": "p1", "name": "p1"}}},
        expected_behavior="reject",
        expected_final_state_diff=ExpectedFinalStateDiff(match_mode="strict"),
        expected_alternative="不允许执行清库级别的危险操作，应拒绝"
    ))

    # 29. complex, multi, clear, standard, success, free
    records.append(GoldenRecord(
        id="golden-029",
        query="复制车间一的页面到车间二，替换其中所有的'WS1'点位标签为'WS2'，然后做下装前校验",
        domain="multi",
        complexity="complex",
        initial_world={
            "pages": {"ws1": {"id": "ws1", "name": "车间一", "widgets": {"w1": {"id": "w1", "page_id": "ws1", "type": "text", "position": [0,0], "size": [10,10], "bindings": {"text": "WS1_Status"}}}}},
            "points": {"WS1_Status": {"tag": "WS1_Status", "type": "digital"}, "WS2_Status": {"tag": "WS2_Status", "type": "digital"}}
        },
        expected_behavior="success",
        expected_final_state_diff=ExpectedFinalStateDiff(
            match_mode="subset",
            added_or_modified={
                "pages.ws2.name": "车间二",
                "pages.ws2.widgets.w1.bindings.text": "WS2_Status",
                "deployments.default.status": "validated"
            }
        ),
        expected_workflow_id="deployment_check"
    ))

    # 30. complex, multi, false premise, colloquial, reject, wf: alarm_config
    records.append(GoldenRecord(
        id="golden-030",
        query="给根本不存在的假点位配上一大堆高低报，然后再开个趋势图看它",
        domain="multi",
        complexity="complex",
        initial_world={},
        expected_behavior="reject",
        expected_final_state_diff=ExpectedFinalStateDiff(match_mode="strict"),
        expected_error_code="POINT_NOT_FOUND",
        expected_workflow_id="alarm_config"
    ))

    with open("eval/golden_dataset.jsonl", "w", encoding="utf-8") as f:
        for r in records:
            f.write(r.model_dump_json(exclude_none=True) + "\n")

if __name__ == "__main__":
    generate()
