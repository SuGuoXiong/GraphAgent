"""DAG 工具模块——拓扑排序、分层分组、依赖校验。"""

from graph_agent.orchestration.state import SubTask


def build_dag(tasks: list[SubTask]) -> dict[str, set[str]]:
    """从任务列表构建邻接表（task_id -> 直接后继集合）。

    注：此函数用于可视化和调试，不参与运行时执行路径。
    """
    dag: dict[str, set[str]] = {t.task_id: set() for t in tasks}
    for t in tasks:
        for dep in t.dependencies:
            dag.setdefault(dep, set()).add(t.task_id)
    return dag


def topological_layers(tasks: list[SubTask]) -> list[list[SubTask]]:
    """将任务列表按拓扑层级分组，每层内的任务无相互依赖。

    算法（Kahn 变体）：
    1. 计算每个任务的入度——仅统计 dependencies 中**也在本批任务集内**的依赖
       （指向已完成任务或不存在任务的依赖不参与拓扑排序，避免入度虚高）
    2. 入度为 0 的任务进入当前层
    3. 执行当前层后，将其后继任务的入度减 1
    4. 入度变为 0 的后继进入下一层
    5. 重复直到所有任务分层完成

    Returns:
        [[layer_0_tasks], [layer_1_tasks], ...]
        同一层内的任务可以并行执行

    Example:
        tasks = [task_1(deps=[]), task_2(deps=[]), task_3(deps=[task_1, task_2])]
        → [[task_1, task_2], [task_3]]

        tasks = [task_3(deps=[task_1]), task_4(deps=[task_2])]
        （其中 task_1, task_2 已完成，不在 tasks 中）
        → [[task_3, task_4]]  ← 入度均为 0，归入同一层并行执行
    """
    if not tasks:
        return []

    task_ids = {t.task_id for t in tasks}
    task_map = {t.task_id: t for t in tasks}

    # 入度：仅统计 dependencies 中属于本批任务集的依赖
    in_degree: dict[str, int] = {}
    for t in tasks:
        internal_deps = [d for d in t.dependencies if d in task_ids]
        in_degree[t.task_id] = len(internal_deps)

    # 后继关系：仅记录本批任务集内部的后继
    successors: dict[str, list[str]] = {t.task_id: [] for t in tasks}
    for t in tasks:
        for dep in t.dependencies:
            if dep in task_ids:
                successors.setdefault(dep, []).append(t.task_id)

    layers = []
    current = [t for t in tasks if in_degree[t.task_id] == 0]

    while current:
        layers.append(current)
        next_layer = []
        for t in current:
            for succ_id in successors.get(t.task_id, []):
                in_degree[succ_id] -= 1
                if in_degree[succ_id] == 0:
                    next_layer.append(task_map[succ_id])
        current = next_layer

    return layers


def validate_dag(tasks: list[SubTask]) -> list[str]:
    """校验任务依赖图是否为合法的 DAG（无环、无缺失依赖）。

    Returns:
        错误信息列表，空列表表示校验通过
    """
    errors = []
    task_ids = {t.task_id for t in tasks}

    # 检查所有依赖是否指向存在的任务
    for t in tasks:
        for dep in t.dependencies:
            if dep not in task_ids:
                errors.append(f"任务 '{t.task_id}' 依赖不存在的任务 '{dep}'")

    # 检查是否存在循环依赖（DFS 三色标记法）
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {t.task_id: WHITE for t in tasks}

    def dfs(task_id: str) -> bool:
        """返回 True 表示发现环。"""
        color[task_id] = GRAY
        task = next((t for t in tasks if t.task_id == task_id), None)
        if task:
            for dep in task.dependencies:
                c = color.get(dep, BLACK)
                if c == GRAY:
                    errors.append(f"检测到循环依赖: {task_id} -> {dep}")
                    return True
                if c == WHITE:
                    if dfs(dep):
                        return True
        color[task_id] = BLACK
        return False

    for t in tasks:
        if color[t.task_id] == WHITE:
            dfs(t.task_id)

    return errors


def validate_and_log(tasks: list[SubTask], logger=None) -> bool:
    """校验 DAG 合法性并在失败时记录日志。

    在 _generate_plan 和 _dispatch_tasks 中调用。

    Returns:
        True 表示通过校验
    """
    errors = validate_dag(tasks)
    if errors:
        for err in errors:
            if logger:
                logger.warning(f"[DAG Validation] {err}")
        return False
    return True
