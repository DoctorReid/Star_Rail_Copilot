import time
from typing import List, ClassVar, Optional, Callable

from cv2.typing import MatLike

from basic.i18_utils import gt
from basic.img import cv2_utils
from basic.log_utils import log
from sr.context import Context
from sr.image.sceenshot import mini_map, screen_state, MiniMapInfo
from sr.operation import StateOperation, StateOperationEdge, StateOperationNode, OperationOneRoundResult, Operation, \
    OperationResult
from sr.sim_uni.op.move_in_sim_uni import MoveToNextLevel
from sr.sim_uni.op.sim_uni_battle import SimUniEnterFight, SimUniFightElite
from sr.sim_uni.op.v2.sim_uni_move_v2 import SimUniMoveToEnemyByMiniMap, SimUniMoveToEnemyByDetect
from sr.sim_uni.sim_uni_const import SimUniLevelTypeEnum
from sryolo.detector import DetectResult


class SimUniRunRouteBase(StateOperation):

    STATUS_WITH_RED: ClassVar[str] = '小地图有红点'
    STATUS_NO_RED: ClassVar[str] = '小地图无红点'
    STATUS_WITH_ENEMY: ClassVar[str] = '识别到敌人'
    STATUS_NO_ENEMY: ClassVar[str] = '识别不到敌人'
    STATUS_WITH_ENTRY: ClassVar[str] = '识别到下层入口'
    STATUS_NO_ENTRY: ClassVar[str] = '识别不到下层入口'
    STATUS_NOTHING: ClassVar[str] = '识别不到任何内容'

    def __init__(self, ctx: Context, op_name: str, try_times: int = 2,
                 nodes: Optional[List[StateOperationNode]] = None,
                 edges: Optional[List[StateOperationEdge]] = None,
                 specified_start_node: Optional[StateOperationNode] = None,
                 timeout_seconds: float = -1,
                 op_callback: Optional[Callable[[OperationResult], None]] = None):
        StateOperation.__init__(self,
                                ctx=ctx, op_name=op_name, try_times=try_times,
                                nodes=nodes, edges=edges, specified_start_node=specified_start_node,
                                timeout_seconds=timeout_seconds, op_callback=op_callback)

        self.nothing_times: int = 0  # 识别不到任何内容的次数

    def _check_next_entry(self) -> OperationOneRoundResult:
        """
        找下层入口 主要判断能不能找到
        :return:
        """
        screen: MatLike = self.screenshot()
        entry_list = MoveToNextLevel.get_next_level_type(screen, self.ctx.ih)
        if len(entry_list) == 0:
            return Operation.round_success(status=SimUniRunRouteBase.STATUS_NO_ENTRY)
        else:
            self.nothing_times = 0
            return Operation.round_success(status=SimUniRunRouteBase.STATUS_WITH_ENTRY)

    def _move_to_next(self):
        """
        朝下层移动
        :return:
        """
        op = MoveToNextLevel(self.ctx, level_type=SimUniLevelTypeEnum.COMBAT.value)
        return Operation.round_by_op(op.execute())


class SimUniRunCombatRouteV2(SimUniRunRouteBase):

    def __init__(self, ctx: Context):
        """
        区域-战斗
        1. 检测地图是否有红点
        2. 如果有红点 移动到最近的红点 并进行攻击。攻击一次后回到步骤1判断。
        3. 如果没有红点 识别敌对物种位置，向最大的移动，并进行攻击。攻击一次后回到步骤1判断。
        4. 如果没有红点也没有识别到敌对物种，检测下层入口位置，发现后进入下层移动。未发现则选择视角返回步骤1判断。
        """
        edges: List[StateOperationEdge] = []

        check = StateOperationNode('画面检测', self._check_screen)
        move_by_red = StateOperationNode('向红点移动', self._move_by_red)
        edges.append(StateOperationEdge(check, move_by_red, status=SimUniRunRouteBase.STATUS_WITH_RED))

        fight = StateOperationNode('进入战斗', self._enter_fight)
        # 到达红点
        edges.append(StateOperationEdge(move_by_red, fight, status=SimUniMoveToEnemyByMiniMap.STATUS_ARRIVAL))
        # 进行了战斗 就重新开始
        after_fight = StateOperationNode('战斗后处理', self._after_fight)
        edges.append(StateOperationEdge(after_fight, check))
        edges.append(StateOperationEdge(fight, after_fight))
        edges.append(StateOperationEdge(move_by_red, after_fight, status=SimUniMoveToEnemyByMiniMap.STATUS_FIGHT))

        # 小地图没有红点 就在画面上找敌人
        detect_enemy = StateOperationNode('识别敌人', self._detect_enemy_in_screen)
        edges.append(StateOperationEdge(check, detect_enemy, status=SimUniRunRouteBase.STATUS_NO_RED))
        # 找到了敌人就开始移动
        move_by_detect = StateOperationNode('向敌人移动', self._move_by_detect)
        edges.append(StateOperationEdge(detect_enemy, move_by_detect, status=SimUniRunRouteBase.STATUS_WITH_ENEMY))
        # 进入了战斗 就重新开始
        edges.append(StateOperationEdge(move_by_detect, after_fight, status=SimUniMoveToEnemyByDetect.STATUS_FIGHT))

        # 画面上也找不到敌人 就找下层入口
        check_entry = StateOperationNode('识别下层入口', self._check_next_entry)
        edges.append(StateOperationEdge(detect_enemy, check_entry, status=SimUniRunRouteBase.STATUS_NO_ENEMY))
        # 找到了下层入口就开始移动
        move_to_next = StateOperationNode('向下层移动', self._move_to_next)
        edges.append(StateOperationEdge(check_entry, move_to_next, status=SimUniRunRouteBase.STATUS_WITH_ENTRY))
        # 找不到下层入口就转向找目标
        turn = StateOperationNode('转动找目标', self._turn_when_nothing)
        edges.append(StateOperationEdge(check_entry, turn, status=SimUniRunRouteBase.STATUS_NO_ENTRY))
        # 转动完重新开始目标识别
        edges.append(StateOperationEdge(turn, check))

        super().__init__(ctx,
                         op_name=gt('区域-战斗', 'ui'),
                         edges=edges,
                         specified_start_node=check)

        self.last_state: str = ''  # 上一次的画面状态
        self.current_state: str = ''  # 这一次的画面状态

        self.view_down: bool = False  # 将鼠标稍微往下移动 从俯视角度看 更方便看到敌人

    def _check_screen(self) -> OperationOneRoundResult:
        """
        检测屏幕
        :return:
        """
        screen = self.screenshot()

        # 为了保证及时攻击 外层仅判断是否在大世界画面 非大世界画面时再细分处理
        self.current_state = screen_state.get_sim_uni_screen_state(
            screen, self.ctx.im, self.ctx.ocr,
            in_world=True, battle=True)
        log.debug('当前画面 %s', self.current_state)

        if self.current_state == screen_state.ScreenState.NORMAL_IN_WORLD.value:
            return self._handle_in_world(screen)
        else:
            return self._handle_not_in_world(screen)

    def _handle_in_world(self, screen: MatLike) -> OperationOneRoundResult:
        mm = mini_map.cut_mini_map(screen, self.ctx.game_config.mini_map_pos)
        mm_info: MiniMapInfo = mini_map.analyse_mini_map(mm)

        if mini_map.is_under_attack_new(mm_info):
            op = SimUniEnterFight(self.ctx)
            return Operation.round_wait_by_op(op.execute())

        pos_list = mini_map.get_enemy_pos(mm_info)

        if len(pos_list) == 0:
            return Operation.round_success(status=SimUniRunRouteBase.STATUS_NO_RED)
        else:
            return Operation.round_success(status=SimUniRunRouteBase.STATUS_WITH_RED)

    def _move_by_red(self) -> OperationOneRoundResult:
        """
        朝小地图红点走去
        :return:
        """
        self.nothing_times = 0
        op = SimUniMoveToEnemyByMiniMap(self.ctx)
        return Operation.round_by_op(op.execute())

    def _enter_fight(self) -> OperationOneRoundResult:
        op = SimUniEnterFight(self.ctx,
                              first_state=screen_state.ScreenState.NORMAL_IN_WORLD.value,
                              )
        return op.round_by_op(op.execute())

    def _after_fight(self) -> OperationOneRoundResult:
        self.view_down = False  # 每次战斗后 游戏中都会重置视角
        return Operation.round_success()

    def _handle_not_in_world(self, screen: MatLike) -> OperationOneRoundResult:
        """
        不在大世界的场景 无论是什么 都可以交给 SimUniEnterFight 处理
        :param screen:
        :return:
        """
        op = SimUniEnterFight(self.ctx, config=self.ctx.sim_uni_challenge_config)
        return Operation.round_wait_by_op(op.execute())

    def _detect_enemy_in_screen(self) -> OperationOneRoundResult:
        """
        没有红点时 判断当前画面是否有怪
        TODO 之后可以把入口识别也放到这里
        :return:
        """
        if not self.view_down:
            self.ctx.controller.turn_down(25)
            self.view_down = True
            return Operation.round_wait(wait=0.2)

        screen: MatLike = self.screenshot()
        self.ctx.init_yolo()

        detect_results: List[DetectResult] = self.ctx.yolo.detect(screen)

        with_enemy: bool = False
        for result in detect_results:
            if result.detect_class.class_cate == '普通怪':
                with_enemy = True
                break

        if with_enemy:
            return Operation.round_success(status=SimUniRunRouteBase.STATUS_WITH_ENEMY)
        else:
            cv2_utils.show_image(screen, win_name='_detect_enemy_in_screen')
            return Operation.round_success(SimUniRunRouteBase.STATUS_NO_ENEMY)

    def _move_by_detect(self) -> OperationOneRoundResult:
        """
        识别到敌人人开始移动
        :return:
        """
        self.nothing_times = 0
        op = SimUniMoveToEnemyByDetect(self.ctx)
        return Operation.round_by_op(op.execute())

    def _turn_when_nothing(self) -> OperationOneRoundResult:
        """
        当前画面识别不到任何内容时候 转动一下
        :return:
        """
        self.nothing_times += 1
        if self.nothing_times >= 12:
            return Operation.round_fail(SimUniRunRouteBase.STATUS_NOTHING)

        # angle = (25 + 10 * self.nothing_times) * (1 if self.nothing_times % 2 == 0 else -1)  # 来回转动视角
        # 由于攻击之后 人物可能朝反方向了 因此要转动多一点
        # 不要被360整除 否则转一圈之后还是被人物覆盖了看不到
        angle = 35
        self.ctx.controller.turn_by_angle(angle)
        return Operation.round_success(wait=0.5)


class SimUniRunEliteRouteV2(SimUniRunRouteBase):

    def __init__(self, ctx: Context):
        """
        区域-精英
        1. 检查小地图是否有红点 有就向红点移动
        2. 开怪
        3. 领取奖励
        4. 朝下层移动
        :param ctx:
        """
        edges: List[StateOperationEdge] = []

        check_red = StateOperationNode('识别小地图红点', self._check_red)

        # 有红点就靠红点移动
        move_by_red = StateOperationNode('向红点移动', self._move_by_red)
        edges.append(StateOperationEdge(check_red, move_by_red, status=SimUniRunRouteBase.STATUS_WITH_RED))

        # TODO 没有红点暂时不处理

        # 到达精英怪旁边发起攻击
        start_fight = StateOperationNode('发起攻击', self._start_fight)
        edges.append(StateOperationEdge(move_by_red, start_fight, status=SimUniMoveToEnemyByMiniMap.STATUS_ARRIVAL))

        # TODO 暂时没有领取奖励处理

        # 识别下层入口
        check_entry = StateOperationNode('识别下层入口', self._check_next_entry)
        edges.append(StateOperationEdge(start_fight, check_entry, status=SimUniRunRouteBase.STATUS_NO_ENEMY))
        # 找到了下层入口就开始移动
        move_to_next = StateOperationNode('向下层移动', self._move_to_next)
        edges.append(StateOperationEdge(check_entry, move_to_next, status=SimUniRunRouteBase.STATUS_WITH_ENTRY))

        super().__init__(ctx,
                         op_name=gt('区域-精英', 'ui'),
                         edges=edges,
                         # specified_start_node=check
                         )

    def _check_red(self) -> OperationOneRoundResult:
        """
        检查小地图是否有红点
        :return:
        """
        screen = self.screenshot()
        mm = mini_map.cut_mini_map(screen, self.ctx.game_config.mini_map_pos)
        mm_info = mini_map.analyse_mini_map(mm, self.ctx.im)
        pos_list = mini_map.get_enemy_pos(mm_info)
        if len(pos_list) == 0:
            return Operation.round_success(status=SimUniRunRouteBase.STATUS_NO_RED)
        else:
            return Operation.round_success(status=SimUniRunRouteBase.STATUS_WITH_RED)

    def _move_by_red(self) -> OperationOneRoundResult:
        """
        往小地图红点移动
        :return:
        """
        op = SimUniMoveToEnemyByMiniMap(self.ctx, no_attack=True)
        return Operation.round_by_op(op.execute())

    def _start_fight(self) -> OperationOneRoundResult:
        """
        移动到精英怪旁边之后 发起攻击
        :return:
        """
        op = SimUniFightElite(self.ctx)
        return Operation.round_by_op(op.execute())


