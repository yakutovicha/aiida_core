# -*- coding: utf-8 -*-
###########################################################################
# Copyright (c), The AiiDA team. All rights reserved.                     #
# This file is part of the AiiDA code.                                    #
#                                                                         #
# The code is hosted on GitHub at https://github.com/aiidateam/aiida_core #
# For further information on the license, see the LICENSE.txt file        #
# For further information please visit http://www.aiida.net               #
###########################################################################
from aiida.orm.data.int import Int
from aiida.orm.data.list import List
from aiida.orm.data.str import Str 
from aiida.orm.calculation.inline import make_inline
from aiida.work import submit
from aiida.work.workchain import WorkChain, ToContext, append_

class NestedWorkChain(WorkChain):
    """
    Nested workchain which creates a workflow where the nesting level is equal to its input.
    """
    @classmethod
    def define(cls, spec):
        super(NestedWorkChain, cls).define(spec)
        spec.input('inp', valid_type=Int)
        spec.outline(
            cls.do_submit,
            cls.finalize
        )
        spec.output('output', valid_type=Int, required=True)

    def do_submit(self):
        if self.should_submit():
            self.report('Submitting nested workchain.')
            return ToContext(
                workchain=append_(self.submit(
                    NestedWorkChain,
                    inp=self.inputs.inp - 1
                ))
            )

    def should_submit(self):
        return int(self.inputs.inp) > 0

    def finalize(self):
        if self.should_submit():
            self.report('Getting sub-workchain output.')
            sub_workchain = self.ctx.workchain[0]
            self.out('output', sub_workchain.out.output + 1)
        else:
            self.report('Bottom-level workchain reached.')
            self.out('output', Int(0))

class ListEcho(WorkChain):
    @classmethod
    def define(cls, spec):
        super(ListEcho, cls).define(spec)

        spec.input('list', valid_type=List)
        spec.output('output', valid_type=List)

        spec.outline(cls.do_echo)

    def do_echo(self):
        self.out('output', self.inputs.list)

class InlineCalcRunnerWorkChain(WorkChain):
    @classmethod
    def define(cls, spec):
        super(InlineCalcRunnerWorkChain, cls).define(spec)

        spec.input('input', valid_type=Str)
        spec.output('output', valid_type=Str)

        spec.outline(cls.do_run)

    def do_run(self):
        self.out('output', echo_inline(input_string=self.inputs.input)[1]['output'])

@make_inline
def echo_inline(input_string):
    return {'output': input_string}
