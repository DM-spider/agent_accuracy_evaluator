# -*- coding: utf-8 -*-
from golden_builder.builders.bj import BUILDERS as BJ
from golden_builder.builders.cx import BUILDERS as CX
from golden_builder.builders.dma import BUILDERS as DMA
from golden_builder.builders.jl import BUILDERS as JL
from golden_builder.builders.ls import BUILDERS as LS

BUILDERS = {}
BUILDERS.update(CX)
BUILDERS.update(LS)
BUILDERS.update(DMA)
BUILDERS.update(JL)
BUILDERS.update(BJ)
