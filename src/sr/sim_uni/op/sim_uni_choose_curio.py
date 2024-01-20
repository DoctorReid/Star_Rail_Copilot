import time
from typing import Optional, ClassVar, List

from cv2.typing import MatLike

from basic import Rect
from basic.i18_utils import gt
from basic.img import MatchResult, cv2_utils
from basic.log_utils import log
from sr.context import Context
from sr.image.sceenshot import screen_state
from sr.operation import Operation, OperationOneRoundResult, StateOperation, StateOperationNode, StateOperationEdge
from sr.sim_uni.sim_uni_const import match_best_curio_by_ocr, SimUniCurio, SimUniCurioEnum
from sr.sim_uni.sim_uni_priority import SimUniCurioPriority


class SimUniChooseCurio(StateOperation):
    # 奇物名字对应的框 - 3个的情况
    CURIO_RECT_3_LIST: ClassVar[List[Rect]] = [
        Rect(315, 280, 665, 320),
        Rect(780, 280, 1120, 320),
        Rect(1255, 280, 1590, 320),
    ]

    # 奇物名字对应的框 - 2个的情况
    CURIO_RECT_2_LIST: ClassVar[List[Rect]] = [
        Rect(513, 280, 933, 320),
        Rect(1024, 280, 1363, 320),
    ]

    # 奇物名字对应的框 - 1个的情况
    CURIO_RECT_1_LIST: ClassVar[List[Rect]] = [
        Rect(780, 280, 1120, 320),
    ]

    CONFIRM_BTN: ClassVar[Rect] = Rect(1500, 950, 1840, 1000)  # 确认选择

    def __init__(self, ctx: Context, priority: Optional[SimUniCurioPriority] = None,
                 skip_first_screen_check: bool = True):
        """
        模拟宇宙中 选择奇物
        :param ctx:
        :param priority: 奇物优先级
        :param skip_first_screen_check: 是否跳过第一次画面状态检查
        """
        edges = []

        choose_curio = StateOperationNode('选择奇物', self._choose_curio)
        check_screen_state = StateOperationNode('游戏画面', self._check_after_confirm)
        edges.append(StateOperationEdge(choose_curio, check_screen_state))

        empty_to_continue = StateOperationNode('点击空白处关闭', self._click_empty_to_continue)
        edges.append(StateOperationEdge(check_screen_state, empty_to_continue,
                                        status='点击空白处关闭'))
        edges.append(StateOperationEdge(empty_to_continue, check_screen_state))

        super().__init__(ctx, try_times=5,
                         op_name='%s %s' % (gt('模拟宇宙', 'ui'), gt('选择奇物', 'ui')),
                         edges=edges,
                         specified_start_node=choose_curio,
                         # specified_start_node=check_screen_state,
                         )

        self.priority: Optional[SimUniCurioPriority] = priority
        self.skip_first_screen_check: bool = skip_first_screen_check  # 是否跳过第一次的画面状态检查 用于提速
        self.first_screen_check: bool = True  # 是否第一次检查画面状态

    def _init_before_execute(self):
        super()._init_before_execute()
        self.first_screen_check = True

    def _choose_curio(self) -> OperationOneRoundResult:
        screen = self.screenshot()

        if not self.first_screen_check or not self.skip_first_screen_check:
            self.first_screen_check = False
            if not screen_state.in_sim_uni_choose_curio(screen, self.ctx.ocr):
                return Operation.round_retry('未在模拟宇宙-选择奇物页面')

        curio_pos_list: List[MatchResult] = self._get_curio_pos(screen)
        if len(curio_pos_list) == 0:
            return Operation.round_retry('未识别到奇物', wait=1)

        target_curio_pos: Optional[MatchResult] = self._get_curio_to_choose(curio_pos_list)
        self.ctx.controller.click(target_curio_pos.center)
        time.sleep(0.25)
        self.ctx.controller.click(SimUniChooseCurio.CONFIRM_BTN.center)
        return Operation.round_success(wait=2)

    def _get_curio_pos(self, screen: MatLike) -> List[MatchResult]:
        """
        获取屏幕上的奇物的位置
        :param screen: 屏幕截图
        :return: MatchResult.data 中是对应的奇物 SimUniCurio
        """
        curio_list = self._get_curio_pos_by_rect(screen, SimUniChooseCurio.CURIO_RECT_3_LIST)
        if len(curio_list) > 0:
            return curio_list

        curio_list = self._get_curio_pos_by_rect(screen, SimUniChooseCurio.CURIO_RECT_2_LIST)
        if len(curio_list) > 0:
            return curio_list

        curio_list = self._get_curio_pos_by_rect(screen, SimUniChooseCurio.CURIO_RECT_1_LIST)
        if len(curio_list) > 0:
            return curio_list

        return []

    def _get_curio_pos_by_rect(self, screen: MatLike, rect_list: List[Rect]) -> List[MatchResult]:
        """
        获取屏幕上的奇物的位置
        :param screen: 屏幕截图
        :param rect_list: 指定区域
        :return: MatchResult.data 中是对应的奇物 SimUniCurio
        """
        curio_list: List[MatchResult] = []

        for rect in rect_list:
            title_part = cv2_utils.crop_image_only(screen, rect)
            title_ocr = self.ctx.ocr.ocr_for_single_line(title_part)
            # cv2_utils.show_image(title_part, wait=0)

            curio = match_best_curio_by_ocr(title_ocr)

            if curio is None:  # 有一个识别不到就返回 提速
                return curio_list

            log.info('识别到奇物 %s', curio)
            curio_list.append(MatchResult(1,
                                          rect.x1, rect.y1,
                                          rect.width, rect.height,
                                          data=curio))

        return curio_list

    def _get_curio_to_choose(self, curio_pos_list: List[MatchResult]) -> Optional[MatchResult]:
        """
        根据优先级选择对应的奇物
        :param curio_pos_list: 奇物列表
        :return:
        """
        curio_list = [curio.data for curio in curio_pos_list]
        target_idx = SimUniChooseCurio.get_curio_by_priority(curio_list, self.priority)
        if target_idx is None:
            return None
        else:
            return curio_pos_list[target_idx]

    @staticmethod
    def get_curio_by_priority(curio_list: List[SimUniCurio], priority: Optional[SimUniCurioPriority]) -> Optional[int]:
        """
        根据优先级选择对应的奇物
        :param curio_list: 可选的奇物列表
        :param priority: 优先级
        :return: 选择的下标
        """
        if priority is None:
            return 0

        for curio in SimUniCurioEnum:
            for idx, opt_curio in enumerate(curio_list):
                if curio.value == opt_curio.data:
                    return idx

        return 0

    def _check_after_confirm(self) -> OperationOneRoundResult:
        """
        确认后判断下一步动作
        :return:
        """
        screen = self.screenshot()
        state = self._get_screen_state(screen)
        if state is None:
            return Operation.round_retry('未能判断当前页面', wait=1)
        else:
            return Operation.round_success(state)

    def _get_screen_state(self, screen: MatLike) -> Optional[str]:
        state = screen_state.get_sim_uni_screen_state(screen, self.ctx.im, self.ctx.ocr,
                                                      in_world=True,
                                                      empty_to_close=True,
                                                      bless=True,
                                                      curio=True)
        if state is not None:
            return state

        # 未知情况都先点击一下
        log.info('未能识别当前画面状态')
        self.ctx.controller.click(screen_state.TargetRect.EMPTY_TO_CLOSE.value.center)

        return None

    def _click_empty_to_continue(self) -> OperationOneRoundResult:
        click = self.ctx.controller.click(screen_state.TargetRect.EMPTY_TO_CLOSE.value.center)

        if click:
            return Operation.round_success()
        else:
            return Operation.round_retry('点击空白处关闭失败')
