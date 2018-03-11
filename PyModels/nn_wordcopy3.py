# -*- coding: utf-8 -*-
"""
Модель для выбора слов, которые надо скопировать из предпосылки для формирования
ответа на вопрос.
Модель используется в проекте чат-бота https://github.com/Koziev/chatbot
Датасет должен быть предварительно сгенерирован скриптом prepare_qa_dataset.py
"""

from __future__ import division
from __future__ import print_function

import gc
import itertools
import json
import os
import sys

import gensim
import numpy as np
import pandas as pd
import tqdm

import keras.callbacks
from keras.callbacks import ModelCheckpoint, EarlyStopping
from keras.layers import Conv1D, GlobalMaxPooling1D
from keras.layers import Input
from keras.layers import recurrent
from keras.layers.core import RepeatVector, Dense
from keras.layers.wrappers import Bidirectional
from keras.models import Model
from keras.models import model_from_json

from sklearn.model_selection import train_test_split

from utils.tokenizer import Tokenizer

input_path = '../data/premise_question_answer.csv'
tmp_folder = '../tmp'
data_folder = '../data'

#NET_ARCH = 'lstm'
#NET_ARCH = 'lstm+cnn'
NET_ARCH = 'cnn*lstm'


#BATCH_SIZE = 1000
BATCH_SIZE = 48

# -------------------------------------------------------------------

PAD_WORD = u''

# -------------------------------------------------------------------

RUN_MODE = ''
TRAIN_MODEL = ''

# TODO argparse
while True:
    print('t - train word copy model #3')
    print('q - run queries')
    a1 = raw_input(':> ')

    if a1 == 't':
        RUN_MODE = 'train'
        TRAIN_MODEL = 'word_copy3'
        train_word_copy3 = True
        break
    elif a1 == 'q':
        RUN_MODE = 'query'
        print('Query')
        break
    else:
        print('Unrecognized choice "{}"'.format(a1))



max_inputseq_len = 0
max_outputseq_len = 0 # максимальная длина ответа
all_words = set()
all_chars = set()


# --------------------------------------------------------------------------

wordchar2vector_path = os.path.join(tmp_folder,'wordchar2vector.dat')
print( 'Loading the wordchar2vector model {}'.format(wordchar2vector_path) )
wc2v = gensim.models.KeyedVectors.load_word2vec_format(wordchar2vector_path, binary=False)
wc2v_dims = len(wc2v.syn0[0])
print('wc2v_dims={0}'.format(wc2v_dims))

# --------------------------------------------------------------------------

df = pd.read_csv(input_path, encoding='utf-8', delimiter='\t', quoting=3)

print('samples.count={}'.format(df.shape[0]))

tokenizer = Tokenizer()
for i,record in df.iterrows():
    for phrase in [record['premise'], record['question']]:
        all_chars.update( phrase )
        words = tokenizer.tokenize(phrase)
        all_words.update(words)
        max_inputseq_len = max( max_inputseq_len, len(words) )

    phrase = record['answer']
    all_chars.update(phrase)
    words = tokenizer.tokenize(phrase)
    all_words.update(words)
    max_outputseq_len = max(max_outputseq_len, len(words))
    #answer = record['answer'].lower().strip()


for word in wc2v.vocab:
    all_words.add(word)
    all_chars.update(word)

print('max_inputseq_len={}'.format(max_inputseq_len))
print('max_outputseq_len={}'.format(max_outputseq_len))

word2id = dict( [(c,i) for i,c in enumerate( itertools.chain([PAD_WORD], filter(lambda z:z!=PAD_WORD,all_words)))] )

nb_chars = len(all_chars)
nb_words = len(all_words)
print('nb_chars={}'.format(nb_chars))
print('nb_words={}'.format(nb_words))


# --------------------------------------------------------------------------

#w2v_path = '/home/eek/polygon/w2v/w2v.CBOW=0_WIN=5_DIM=32.txt'
w2v_path = '/home/eek/polygon/w2v/w2v.CBOW=0_WIN=5_DIM=48.txt'
#w2v_path = '/home/eek/polygon/WordSDR2/sdr.dat'
#w2v_path = '/home/eek/polygon/w2v/w2v.CBOW=0_WIN=5_DIM=128.txt'
#w2v_path = r'f:\Word2Vec\word_vectors_cbow=1_win=5_dim=32.txt'
print( 'Loading the w2v model {}'.format(w2v_path) )
w2v = gensim.models.KeyedVectors.load_word2vec_format(w2v_path, binary=False)
w2v_dims = len(w2v.syn0[0])
print('w2v_dims={0}'.format(w2v_dims))

word_dims = w2v_dims+wc2v_dims


word2vec = dict()
for word in wc2v.vocab:
    v = np.zeros( word_dims )
    v[w2v_dims:] = wc2v[word]
    if word in w2v:
        v[:w2v_dims] = w2v[word]

    word2vec[word] = v

del w2v
#del wc2v
gc.collect()
# -------------------------------------------------------------------


print('Constructing the NN model...')

nb_filters = 128
rnn_size = word_dims

final_merge_size = 0

# --------------------------------------------------------------------------------

# сохраним конфиг модели, чтобы ее использовать в чат-боте
model_config = {
                'engine': 'nn',
                'max_inputseq_len': max_inputseq_len,
                'max_outputseq_len': max_outputseq_len,
                'w2v_path': w2v_path,
                'wordchar2vector_path': wordchar2vector_path,
                'PAD_WORD': PAD_WORD,
                'model_folder': tmp_folder,
                'word_dims': word_dims
               }

with open(os.path.join(tmp_folder,'nn_wordcopy3.config'), 'w') as f:
    json.dump(model_config, f)

# ------------------------------------------------------------------

# В этих файлах будем сохранять натренированную сетку
arch_filepath = os.path.join(tmp_folder, 'nn_wordcopy3.arch')
weights_path = os.path.join(tmp_folder, 'nn_wordcopy3.weights')

# ------------------------------------------------------------------

padding = 'left'

if RUN_MODE=='train':

    print('Building the NN computational graph...')

    words_net1 = Input(shape=(max_inputseq_len, word_dims,), dtype='float32', name='input_words1')
    words_net2 = Input(shape=(max_inputseq_len, word_dims,), dtype='float32', name='input_words2')

    if NET_ARCH=='lstm':
        # энкодер на базе LSTM, на выходе которого получаем вектор с упаковкой слов
        # предложения.
        shared_words_rnn = Bidirectional(recurrent.LSTM(rnn_size,
                                                        input_shape=(max_inputseq_len, word_dims),
                                                        return_sequences=False))

        encoder_rnn1 = shared_words_rnn(words_net1)
        encoder_rnn2 = shared_words_rnn(words_net2)

        encoder_merged = keras.layers.concatenate(inputs=[encoder_rnn1, encoder_rnn2])
        encoder_size = rnn_size*2
        encoder_final = Dense(units=int(encoder_size), activation='softmax')(encoder_merged)

    # --------------------------------------------------------------------------

    if NET_ARCH=='lstm+cnn':
        conv1 = []
        conv2 = []
        conv3 = []

        repr_size = 0

        # энкодер на базе LSTM, на выходе которого получаем вектор с упаковкой слов
        # предложения.
        shared_words_rnn = Bidirectional(recurrent.LSTM(rnn_size,
                                                        input_shape=(max_inputseq_len, word_dims),
                                                        return_sequences=False))

        encoder_rnn1 = shared_words_rnn(words_net1)
        encoder_rnn2 = shared_words_rnn(words_net2)

        dense1 = Dense(units=rnn_size*2)
        conv1.append(encoder_rnn1)
        conv2.append(encoder_rnn2)

        repr_size += rnn_size*2

        # добавляем входы со сверточными слоями
        for kernel_size in range(2, 4):
            conv = Conv1D(filters=nb_filters,
                          kernel_size=kernel_size,
                          padding='valid',
                          activation='relu',
                          strides=1)

            #dense2 = Dense(units=nb_filters)

            conv_layer1 = conv(words_net1)
            conv_layer1 = GlobalMaxPooling1D()(conv_layer1)
            #conv_layer1 = dense2(conv_layer1)
            conv1.append(conv_layer1)

            conv_layer2 = conv(words_net2)
            conv_layer2 = GlobalMaxPooling1D()(conv_layer2)
            #conv_layer2 = dense2(conv_layer2)
            conv2.append(conv_layer2)

            repr_size += nb_filters

        encoder_size = repr_size
        encoder_merged = keras.layers.concatenate(inputs=list(itertools.chain(conv1, conv2, conv3)))
        encoder_final = Dense(units=int(encoder_size), activation='relu')(encoder_merged)

    # --------------------------------------------------------------------------

    if NET_ARCH == 'cnn*lstm':

        conv1 = []
        conv2 = []
        encoder_size = 0

        for kernel_size in range(1, 4):
            # сначала идут сверточные слои, образующие детекторы словосочетаний
            # и синтаксических конструкций
            conv = Conv1D(filters=nb_filters,
                          kernel_size=kernel_size,
                          padding='valid',
                          activation='relu',
                          strides=1,
                          name='shared_conv_{}'.format(kernel_size))

            lstm = recurrent.LSTM(rnn_size, return_sequences=False)

            pooler = keras.layers.MaxPooling1D(pool_size=kernel_size, strides=None, padding='valid')

            conv_layer1 = conv(words_net1)
            conv_layer1 = pooler(conv_layer1)
            conv_layer1 = lstm(conv_layer1)
            conv1.append(conv_layer1)

            conv_layer2 = conv(words_net2)
            conv_layer2 = pooler(conv_layer2)
            conv_layer2 = lstm(conv_layer2)
            conv2.append(conv_layer2)

            encoder_size += rnn_size

        encoder_merged = keras.layers.concatenate(inputs=list(itertools.chain(conv1, conv2)))
        encoder_final = Dense(units=int(encoder_size), activation='relu')(encoder_merged)

    # Тренируем модель, которая определяет позиции слов начала и конца цепочки.
    # Таким образом, у модели два независимых классификатора на выходе.
    output_dims = max_inputseq_len

    decoder = Dense(rnn_size, activation='relu')(encoder_final)
    decoder = Dense(rnn_size, activation='relu')(decoder)
    decoder = Dense(rnn_size, activation='relu')(decoder)
    decoder = Dense(rnn_size, activation='relu')(decoder)
    output_beg = Dense(output_dims, activation='softmax', name='output_beg')(decoder)

    decoder = Dense(rnn_size, activation='relu')(encoder_final)
    decoder = Dense(rnn_size, activation='relu')(decoder)
    decoder = Dense(rnn_size, activation='relu')(decoder)
    decoder = Dense(rnn_size, activation='relu')(decoder)
    output_end = Dense(output_dims, activation='softmax', name='output_end')(decoder)

    model = Model(inputs=[words_net1, words_net2], outputs=[output_beg, output_end])
    model.compile(loss='categorical_crossentropy', optimizer='nadam', metrics=['accuracy'])

    padding = 'right'


    with open(arch_filepath, 'w') as f:
        f.write(model.to_json())


# -------------------------------------------------------------------------


# Слева добавляем пустые слова
def pad_wordseq(words, n):
    return list( itertools.chain( itertools.repeat(PAD_WORD, n-len(words)), words ) )

# Справа добавляем пустые слова
def rpad_wordseq(words, n):
    return list( itertools.chain( words, itertools.repeat(PAD_WORD, n-len(words)) ) )

# -------------------------------------------------------------------------

input_data = []
output_data = []

for index, row in tqdm.tqdm(df.iterrows(), total=df.shape[0], desc='Extract phrases'):
    premise = row['premise']
    question = row['question']
    answer = row['answer']

    if padding == 'left':
        premise_words = pad_wordseq( tokenizer.tokenize(premise), max_inputseq_len )
        question_words = pad_wordseq( tokenizer.tokenize(question), max_inputseq_len )
    else:
        premise_words = rpad_wordseq( tokenizer.tokenize(premise), max_inputseq_len )
        question_words = rpad_wordseq( tokenizer.tokenize(question), max_inputseq_len )

    answer_words = tokenizer.tokenize(answer)
    input_data.append( (premise_words, question_words, premise, question) )
    output_data.append( (answer_words, answer) )

SEED = 123456
TEST_SHARE = 0.2
train_input, val_input, train_output, val_output = train_test_split( input_data, output_data, test_size=TEST_SHARE, random_state=SEED )

# -----------------------------------------------------------------


def vectorize_words( words, M, irow, word2vec ):
    for iword,word in enumerate( words ):
        if word in word2vec:
            M[irow, iword, :] = word2vec[word]


# -------------------------------------------------------------------

def select_patterns( sequences, targets ):
    sequences1 = []
    targets1 = []

    for seq, target in itertools.izip(sequences, targets):
        premise = seq[0]
        answer = target[0]

        all_words_mapped = True # все ли слова вопроса найдены в предпосылке?
        for word in answer:
            if word not in premise:
                all_words_mapped = False
                break

        if all_words_mapped:
            sequences1.append(seq)
            targets1.append(target)

    return (sequences1, targets1)



# --------------------------------------------------------------------------------------

def generate_rows_word_copy3(sequences, targets, batch_size, mode):
    batch_index = 0
    batch_count = 0

    X1_batch = np.zeros((batch_size, max_inputseq_len, word_dims), dtype=np.float32)
    X2_batch = np.zeros((batch_size, max_inputseq_len, word_dims), dtype=np.float32)

    y1_batch = np.zeros((batch_size, output_dims), dtype=np.bool)
    y2_batch = np.zeros((batch_size, output_dims), dtype=np.bool)

    while True:
        for irow, (seq, target) in enumerate(itertools.izip(sequences,targets)):
            vectorize_words(seq[0], X1_batch, batch_index, word2vec)
            vectorize_words(seq[1], X2_batch, batch_index, word2vec)

            premise = seq[0]
            answer = target[0]

            beg_found = False
            beg_pos = 0
            end_pos = 0
            for word in answer:
                if word in premise:
                    pos = premise.index(word)
                    if beg_found==False:
                        beg_found = True
                        beg_pos = pos
                    end_pos = max( end_pos, pos)
                else:
                    break

            assert( end_pos>=beg_pos )

            y1_batch[batch_index, beg_pos] = True
            y2_batch[batch_index, end_pos] = True


            batch_index += 1

            if batch_index == batch_size:
                batch_count += 1

                if mode == 1:
                    yield ({'input_words1': X1_batch, 'input_words2': X2_batch}, {'output_beg': y1_batch, 'output_end': y2_batch})
                else:
                    yield {'input_words1': X1_batch, 'input_words2': X2_batch}

                # очищаем матрицы порции для новой порции
                X1_batch.fill(0)
                X2_batch.fill(0)
                y1_batch.fill(0)
                y2_batch.fill(0)
                batch_index = 0


# -----------------------------------------------------------------

class colors:
    ok = '\033[92m'
    fail = '\033[91m'
    close = '\033[0m'

# -----------------------------------------------------------------

def count_words(words):
    return len(filter(lambda z:z!=PAD_WORD,words))


batch_size = BATCH_SIZE

if RUN_MODE=='train':

    train_input1, train_output1 = select_patterns(train_input, train_output)
    val_input1, val_output1 = select_patterns(val_input, val_output)

    nb_train_patterns = len(train_input1)
    nb_valid_patterns = len(val_input1)

    print('Start training using {} patterns for training, {} for validation...'.format(nb_train_patterns, nb_valid_patterns))

    monitor_metric = 'val_output_beg_acc'

    model_checkpoint = ModelCheckpoint(weights_path, monitor=monitor_metric,
                                       verbose=1, save_best_only=True, mode='auto')
    early_stopping = EarlyStopping(monitor=monitor_metric, patience=20, verbose=1, mode='auto')

    callbacks = [model_checkpoint, early_stopping]


    hist = model.fit_generator(generator=generate_rows_word_copy3(train_input1, train_output1, batch_size, 1),
                               steps_per_epoch=int(nb_train_patterns / batch_size),
                               epochs=200,
                               verbose=2,
                               callbacks=callbacks,
                               validation_data=generate_rows_word_copy3(val_input1, val_output1, batch_size, 1),
                               validation_steps=int(nb_valid_patterns / batch_size)
                               )


if RUN_MODE=='query':

    padding = 'right'

    arch_filepath = os.path.join(tmp_folder, 'qa_word_copy3.arch')
    weights_path = os.path.join(tmp_folder, 'qa_word_copy3.weights')

    with open(arch_filepath, 'r') as f:
        model = model_from_json(f.read())

    model.load_weights(weights_path)

    X1_probe = np.zeros((1, max_inputseq_len, word_dims), dtype=np.float32)
    X2_probe = np.zeros((1, max_inputseq_len, word_dims), dtype=np.float32)

    while True:
        print('\nEnter two phrases:')
        phrase1 = raw_input('premise :> ').decode(sys.stdout.encoding).strip().lower()
        if len(phrase1)==0:
            break

        phrase2 = raw_input('question:> ').decode(sys.stdout.encoding).strip().lower()
        if len(phrase2)==0:
            break

        words1 = tokenizer.tokenize(phrase1)
        words2 = tokenizer.tokenize(phrase2)

        if padding=='left':
            words1 = pad_wordseq(words1, max_inputseq_len)
            words2 = pad_wordseq(words2, max_inputseq_len)
        else:
            words1 = rpad_wordseq(words1, max_inputseq_len)
            words2 = rpad_wordseq(words2, max_inputseq_len)

        X1_probe.fill(0)
        X2_probe.fill(0)

        vectorize_words(words1, X1_probe, 0, word2vec )
        vectorize_words(words2, X2_probe, 0, word2vec )
        y_probe = model.predict(x={'input_words1': X1_probe, 'input_words2': X2_probe})

        y1 = y_probe[0][0]
        y2 = y_probe[1][0]

        pos_beg = np.argmax(y1)
        pos_end = np.argmax(y2)

        print('pos_beg={} pos_end={}'.format(pos_beg, pos_end))

        answer_words = words1[pos_beg:pos_end+1]
        print(u'{}'.format(u' '.join(answer_words)))
