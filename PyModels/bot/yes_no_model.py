# -*- coding: utf-8 -*-

import os
from model_applicator import ModelApplicator

class YesNoModel(ModelApplicator):
    def __init__(self):
        pass

    def calc_yes_no(self, premise_str, question_str, text_utils, word_embeddings):
        raise NotImplemented()
