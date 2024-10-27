import time
from typing import Optional

from one_dragon.base.operation.application_run_record import AppRunRecord
from sr_od.app.trailblaze_power.trailblaze_power_config import TrailblazePowerConfig


class TrailblazePowerRunRecord(AppRunRecord):

    def __init__(self, tp_config: TrailblazePowerConfig,
                 instance_idx: Optional[int] = None):
        self.tp_config: TrailblazePowerConfig = tp_config
        AppRunRecord.__init__(self, 'trailblaze_power', instance_idx=instance_idx)

    def check_and_update_status(self):  # 每次都运行
        self.reset_record()
