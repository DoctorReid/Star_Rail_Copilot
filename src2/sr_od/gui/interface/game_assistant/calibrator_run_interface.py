from PySide6.QtWidgets import QWidget
from typing import Optional

from one_dragon.gui.component.row_widget import RowWidget
from one_dragon.gui.view.app_run_interface import AppRunInterface
from sr_od.app.calibrator import Calibrator
from sr_od.app.sr_application import SrApplication
from sr_od.context.sr_context import SrContext


class CalibratorRunInterface(AppRunInterface):

    def __init__(self,
                 ctx: SrContext,
                 parent=None):
        self.ctx: SrContext = ctx
        self.app: Optional[SrApplication] = None

        AppRunInterface.__init__(
            self,
            ctx=ctx,
            object_name='sr_calibrator_run_interface',
            nav_text_cn='校准',
            parent=parent,
        )

    def get_widget_at_top(self) -> QWidget:
        content = RowWidget()

        return content

    def get_app(self) -> SrApplication:
        """
        获取本次运行的app 由子类实现
        由
        :return:
        """
        return Calibrator(self.ctx)