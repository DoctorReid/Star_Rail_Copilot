from typing import List

import sr.const.operation_const
from basic.i18_utils import gt
from basic.log_utils import log
from sr.app.world_patrol import WorldPatrolRouteId, WorldPatrolRoute
from sr.const import operation_const, map_const
from sr.context import Context
from sr.image.sceenshot import LargeMapInfo
from sr.operation import Operation
from sr.operation.combine import CombineOperation
from sr.operation.combine.transport import Transport
from sr.operation.unit.enter_auto_fight import EnterAutoFight
from sr.operation.unit.interact import Interact
from sr.operation.unit.move_directly import MoveDirectly
from sr.operation.unit.wait_in_seconds import WaitInSeconds
from sr.operation.unit.wait_in_world import WaitInWorld


class RunPatrolRoute(CombineOperation):

    def __init__(self, ctx: Context, route_id: WorldPatrolRouteId, first_route: bool = False):
        """
        运行一条锄地路线
        :param ctx:
        :param route_id: 路线ID
        :param first_route: 是否第一条路线
        """
        route: WorldPatrolRoute = WorldPatrolRoute(route_id)
        log.info('准备执行线路 %s', route_id.display_name)
        log.info('感谢以下人员提供本路线 %s', route.author_list)
        super().__init__(ctx, self.init_ops(route, first_route), op_name=gt('锄地路线 %s', 'ui') % route.display_name)

    def init_ops(self, route: WorldPatrolRoute, first_route: bool = False) -> List[Operation]:
        """
        执行这条路线的所有指令
        :param route: 路线实体
        :param first_route: 是否第一条路线
        :return:
        """
        ops: List[Operation] = []

        ops.append(Transport(self.ctx, route.tp, first_route))

        current_pos: tuple = route.tp.lm_pos
        current_lm_info = self.ctx.ih.get_large_map(route.route_id.region)
        for i in range(len(route.route_list)):
            route_item = route.route_list[i]
            next_route_item = route.route_list[i + 1] if i + 1 < len(route.route_list) else None
            op: Operation = None
            if route_item['op'] in [operation_const.OP_MOVE, operation_const.OP_SLOW_MOVE]:
                op, next_pos, next_lm_info = self.move(route_item, next_route_item, current_pos, current_lm_info)
                current_pos = next_pos[:2]
                if next_lm_info is not None:
                    current_lm_info = next_lm_info
            elif route_item['op'] == operation_const.OP_PATROL:
                op = self.patrol()
            elif route_item['op'] == operation_const.OP_INTERACT:
                op = self.interact(route_item['data'])
            elif route_item['op'] == operation_const.OP_WAIT:
                op = self.wait(route_item['data'][0], route_item['data'][1])
            elif route_item['op'] == operation_const.OP_UPDATE_POS:
                next_pos = route_item['data']
                if len(next_pos) > 2:
                    next_region = map_const.region_with_another_floor(current_lm_info.region, next_pos[2])
                    current_lm_info = self.ctx.ih.get_large_map(next_region)
                current_pos = next_pos[:2]
            else:
                log.error('错误的锄大地指令 %s', route_item['op'])

            if op is not None:
                ops.append(op)
            else:
                return None
        return ops

    def move(self, route_item, next_route_item,
             current_pos: tuple, current_lm_info: LargeMapInfo):
        """
        移动到某个点
        :param route_item: 本次指令
        :param next_route_item: 下次指令
        :param current_pos: 当前位置
        :param current_lm_info: 当前楼层大地图信息
        :return:
        """
        target_pos = route_item['data']
        next_lm_info = None
        if len(target_pos) > 2:  # 需要切换层数
            next_region = map_const.region_with_another_floor(current_lm_info.region, target_pos[2])
            next_lm_info = self.ctx.ih.get_large_map(next_region)

        stop_afterwards = next_route_item is None or next_route_item['op'] not in [operation_const.OP_MOVE,
                                                                                   operation_const.OP_SLOW_MOVE]
        no_run = route_item['op'] == operation_const.OP_SLOW_MOVE

        op = MoveDirectly(self.ctx, current_lm_info, next_lm_info=next_lm_info,
                          target=target_pos[:2], start=current_pos,
                          stop_afterwards=stop_afterwards, no_run=no_run)

        return op, target_pos, next_lm_info

    def patrol(self) -> Operation:
        """
        攻击
        :return:
        """
        return EnterAutoFight(self.ctx)

    def interact(self, cn: str) -> Operation:
        """
        交互
        :param cn: 交互文本
        :return:
        """
        return Interact(self.ctx, cn, wait=0)

    def wait(self, wait_type: str, seconds: float) -> Operation:
        """
        等待
        :param wait_type: 等待类型
        :param seconds: 等待秒数
        :return:
        """
        op: Operation = None
        if wait_type == 'in_world':
            op = WaitInWorld(self.ctx, seconds)
        elif wait_type == sr.const.operation_const.WAIT_TYPE_SECONDS:
            op = WaitInSeconds(self.ctx, seconds)
        else:
            log.error('错误的wait类型 %s', wait_type)

        return op