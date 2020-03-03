import texar as tx
from texar.module_base import ModuleBase
import tensorflow as tf
import numpy as np
import os
if os.environ['is_weibo'] == '1':
    from preprocess_weibo.data_utils import pad
    from preprocess_weibo.data_utils import kw_tokenize
else:
    from preprocess.data_utils import pad
    from preprocess.data_utils import kw_tokenize
from utils.log_utils import create_logs, add_logs, add_log
from utils.kg_utils import get_kg_ids_map, load_keyword_kg


class Predictor:
    def __init__(self, config_model, config_data, mode=None, kp_scope_name="pred_net", rr_scope_name="response_retrieval_net"):
        self.model_config = config_model
        self.data_config = config_data
        self.gpu_config = tf.ConfigProto()
        self.gpu_config.gpu_options.allow_growth = True
        self.kp_scope_name = kp_scope_name
        self.rr_scope_name = rr_scope_name
        self.drop_rate = self.model_config._dropout_rate

        self.logs_save_path = self.model_config._log_save_path
        create_logs(self.logs_save_path)

        self.build_data_iterator()
        # build vocab
        self.vocab = self.train_data.vocab(0)

        self.build_keyword_predictor_model()
        self.build_response_retrieval_model()
        #
        # # build keyword knowledge graph(adjacency matrix) for keyword mask generation
        self.build_keyword_kg()

        # load keyword knowledge graph(adjacency matrix & weight matrix) for keyword mask generation
        # self.kg_weight_matrix, self.kg_adjacency_matrix = load_keyword_kg(self.model_config._w_mat_path,
        #                                                                   self.model_config._adj_mat_path)
        # self.kg_matrix_size = int(self.kg_adjacency_matrix.shape[0])
        # self.adj_matrix_size = self.kg_matrix_size
        # self.vocab_ids_to_adj_matrix_ids_map = get_kg_ids_map(self.data_config._keywords_path,
        #                                                       self.data_config._vocab_path)

    def build_data_iterator(self):
        self.train_data = tx.data.MultiAlignedData(self.data_config.data_hparams['train'])
        self.valid_data = tx.data.MultiAlignedData(self.data_config.data_hparams['valid'])
        self.test_data = tx.data.MultiAlignedData(self.data_config.data_hparams['test'])
        self.iterator = tx.data.TrainTestDataIterator(train=self.train_data, val=self.valid_data, test=self.test_data)

    def build_keyword_predictor_model(self):
        with tf.variable_scope(name_or_scope=self.kp_scope_name, reuse=tf.AUTO_REUSE):
            # self.vocab = self.train_data.vocab(0)
            self.context_encoder = tx.modules.UnidirectionalRNNEncoder(hparams=self.model_config.context_encoder_hparams)
            self.prev_predict_layer = tx.modules.MLPTransformConnector(2 * self.data_config._keywords_num,
                                                                       hparams={"activation_fn": "relu",})
            self.predict_layer = tx.modules.MLPTransformConnector(self.data_config._keywords_num)
            self.embedder = tx.modules.WordEmbedder(init_value=self.train_data.embedding_init_value(0).word_vecs,
                                                    hparams=self.model_config.embedder_hparams)
            self.kw_list = self.vocab.map_tokens_to_ids(tf.convert_to_tensor(self.data_config._keywords_candi))
            self.kw_vocab = tx.data.Vocab(self.data_config._keywords_path)
            # self.vocab = tx.data.Vocab(self.data_config._vocab_path) # add by yz

    def build_response_retrieval_model(self):
        with tf.variable_scope(name_or_scope=self.rr_scope_name, reuse=tf.AUTO_REUSE):
            self.source_encoder = tx.modules.HierarchicalRNNEncoder(hparams=self.model_config.source_encoder_hparams)
            self.target_encoder = tx.modules.BidirectionalRNNEncoder(hparams=self.model_config.target_encoder_hparams)
            self.target_kwencoder = tx.modules.BidirectionalRNNEncoder(hparams=self.model_config.target_kwencoder_hparams)
            self.linear_transform = tx.modules.MLPTransformConnector(self.model_config._code_len // 2)
            self.linear_matcher = tx.modules.MLPTransformConnector(1)

    def build_keyword_kg(self):
        if os.environ['is_weibo'] == '1':
            with open("./tx_weibo_data/test/context.txt", "r") as f:
                train_context_keywords_list = [x.strip().split() for x in f.readlines()]
            with open("./tx_weibo_data/test/keywords.txt", "r") as f:
                train_next_keywords_list = [x.strip().split() for x in f.readlines()]

            with open("./tx_weibo_data/valid/context.txt", "r") as f:
                valid_context_keywords_list = [x.strip().split() for x in f.readlines()]
            with open("./tx_weibo_data/valid/keywords.txt", "r") as f:
                valid_next_keywords_list = [x.strip().split() for x in f.readlines()]

            with open("./tx_weibo_data/test/context.txt", "r") as f:
                test_context_keywords_list = [x.strip().split() for x in f.readlines()]
            with open("./tx_weibo_data/test/keywords.txt", "r") as f:
                test_next_keywords_list = [x.strip().split() for x in f.readlines()]
        else:
            with open("./tx_data/train/context.txt", "r") as f:
                train_context_keywords_list = [x.strip().split() for x in f.readlines()]
            with open("./tx_data/train/keywords.txt", "r") as f:
                train_next_keywords_list = [x.strip().split() for x in f.readlines()]

            with open("./tx_data/valid/context.txt", "r") as f:
                valid_context_keywords_list = [x.strip().split() for x in f.readlines()]
            with open("./tx_data/valid/keywords.txt", "r") as f:
                valid_next_keywords_list = [x.strip().split() for x in f.readlines()]

            with open("./tx_data/test/context.txt", "r") as f:
                test_context_keywords_list = [x.strip().split() for x in f.readlines()]
            with open("./tx_data/test/keywords.txt", "r") as f:
                test_next_keywords_list = [x.strip().split() for x in f.readlines()]

        # stoi_dict: dict mapping string(i.e. keyword) into adjacency matrix id
        # vocab_id_to_adj_matrix_id_dict: dict mapping vocab id into adjacency matrix id
        stoi_dict = {}
        vocab_id_to_adj_matrix_id_dict = {}

        keywords_vocab_list = ['<PAD>']
        # [4:] to remove the special tokens('<PAD>', '<BOS>', '<EOS>', '<UNK>') in kw_vocab
        sorted_kw_vocab_items = sorted(self.kw_vocab.id_to_token_map_py.items(),key=lambda x:x[0])[4:]
        keywords_vocab_list.extend([item[1] for item in sorted_kw_vocab_items])
        self.adj_matrix_size = len(keywords_vocab_list)
        self.train_kg_adjacency_matrix = np.zeros(shape=[self.adj_matrix_size, self.adj_matrix_size], dtype=np.float32)
        self.valid_kg_adjacency_matrix = np.zeros(shape=[self.adj_matrix_size, self.adj_matrix_size], dtype=np.float32)
        self.test_kg_adjacency_matrix = np.zeros(shape=[self.adj_matrix_size, self.adj_matrix_size], dtype=np.float32)
        # add a large negative value to be easy to generate keyword mask
        self.train_kg_adjacency_matrix += -1e8
        self.valid_kg_adjacency_matrix += -1e8
        self.test_kg_adjacency_matrix += -1e8
        # self.kg_adjacency_matrix += float('-inf')
        for idx, keyword in enumerate(keywords_vocab_list):
            stoi_dict[keyword] = idx
            vocab_id_to_adj_matrix_id_dict[int(self.vocab.map_tokens_to_ids_py(keyword))] = idx

        ## save dict for debugging
        # import json
        # new_vocab_token_to_id_map_py = {k: str(v) for k, v in self.vocab.token_to_id_map_py.items()}
        # vocab_id_to_adj_matrix_id_dict = {k: str(v) for k, v in vocab_id_to_adj_matrix_id_dict.items()}
        # new_kwvocab_token_to_id_map_py = {k: str(v) for k, v in self.kw_vocab.token_to_id_map_py.items()}
        # with open('./vocab_dict.json', 'w') as f:
        #     json.dump(new_vocab_token_to_id_map_py, f, indent=4)
        # with open('./id2id_dict.json', 'w') as f:
        #     json.dump(vocab_id_to_adj_matrix_id_dict, f, indent=4)
        # with open('./kwvocab_dict.json', 'w') as f:
        #     json.dump(new_kwvocab_token_to_id_map_py, f, indent=4)
        # exit()

        self.vocab_ids_to_adj_matrix_ids_map = tf.contrib.lookup.HashTable(
            tf.contrib.lookup.KeyValueTensorInitializer(
                list(vocab_id_to_adj_matrix_id_dict.keys()), list(vocab_id_to_adj_matrix_id_dict.values()),
                key_dtype=tf.int64, value_dtype=tf.int64
            ),
            default_value=-1
        )

        for context_keywords, next_keywords in zip(train_context_keywords_list, train_next_keywords_list):
            for ckw in context_keywords:
                ckw_idx = stoi_dict[ckw]
                for nkw in next_keywords:
                    nkw_idx = stoi_dict[nkw]
                    self.train_kg_adjacency_matrix[ckw_idx][nkw_idx] = 1.

        for context_keywords, next_keywords in zip(valid_context_keywords_list, valid_next_keywords_list):
            for ckw in context_keywords:
                ckw_idx = stoi_dict[ckw]
                for nkw in next_keywords:
                    nkw_idx = stoi_dict[nkw]
                    self.valid_kg_adjacency_matrix[ckw_idx][nkw_idx] = 1.

        for context_keywords, next_keywords in zip(test_context_keywords_list, test_next_keywords_list):
            for ckw in context_keywords:
                ckw_idx = stoi_dict[ckw]
                for nkw in next_keywords:
                    nkw_idx = stoi_dict[nkw]
                    self.test_kg_adjacency_matrix[ckw_idx][nkw_idx] = 1.

        self.train_kg_adjacency_matrix = tf.convert_to_tensor(self.train_kg_adjacency_matrix)
        self.valid_kg_adjacency_matrix = tf.convert_to_tensor(self.valid_kg_adjacency_matrix)
        self.test_kg_adjacency_matrix = tf.convert_to_tensor(self.test_kg_adjacency_matrix)
        # self.train_kg_adjacency_matrix = tf.constant(self.train_kg_adjacency_matrix)
        # self.valid_kg_adjacency_matrix = tf.constant(self.valid_kg_adjacency_matrix)
        # self.test_kg_adjacency_matrix = tf.constant(self.test_kg_adjacency_matrix)



    def generate_keyword_mask(self, context_ids):
        """Generate mask to only keep the related keywords' Q-value.
        """
        # self.kg_adjacency_matrix = tf.cond(pred=tf.equal(tx.global_mode(),tf.estimator.ModeKeys.TRAIN ),
        #                                                   true_fn=lambda: self.train_kg_adjacency_matrix,
        #                                                   false_fn=lambda: self.valid_kg_adjacency_matrix)
        # self.kg_adjacency_matrix = tf.cond(pred=tf.equal(tx.global_mode(),tf.estimator.ModeKeys.EVAL ),
        #                                                   true_fn=lambda: self.kg_adjacency_matrix,
        #                                                   false_fn=lambda: self.test_kg_adjacency_matrix)
        self.kg_adjacency_matrix = tf.cond(pred=tf.equal(tx.global_mode(),tf.estimator.ModeKeys.TRAIN ),
                                           true_fn=lambda: self.train_kg_adjacency_matrix,
                                           false_fn=lambda: tf.cond(pred=tf.equal(tx.global_mode(),tf.estimator.ModeKeys.EVAL),
                                                                    true_fn=lambda: self.valid_kg_adjacency_matrix,
                                                                    false_fn=lambda: self.test_kg_adjacency_matrix))

        context_ids = tf.cast(context_ids, tf.int64)
        # shape of adj_matrix_context_ids: [_cur_keywords_len,]
        adj_matrix_context_ids = self.map_vocab_ids_to_adj_matrix_ids(context_ids)
        # shape of context_related_adj_matrix: [#adj_matrix_context_ids, adj_matrix_size]
        context_related_adj_matrix = tf.gather(self.kg_adjacency_matrix, adj_matrix_context_ids)
        # shape of keyword_mask: [adj_matrix_size,]
        keyword_mask = tf.reduce_max(context_related_adj_matrix, axis=0)

        num_related_keywords = tf.reduce_sum(
            tf.cast(
                tf.equal(keyword_mask, 1.), tf.float32
            )
        )
        no_related_keywords = tf.equal(num_related_keywords, 0.)
        ones_tensor = tf.ones(shape=[self.adj_matrix_size])
        # ones_tensor = tf.ones(shape=[self.kg_matrix_size])
        # if no related keywords for current context, keep all keywords' Q-value
        keyword_mask = tf.cond(pred=no_related_keywords,
                               true_fn=lambda: ones_tensor,
                               false_fn=lambda: keyword_mask)
        # remove the <PAD> dimension
        keyword_mask = keyword_mask[1:]

        return keyword_mask

    def map_vocab_ids_to_adj_matrix_ids(self, tokens):
        """Maps text tokens into ids in the adjacency matrix scope.

        The returned ids are a Tensor.

        Args:
            tokens: An tensor of text tokens.

        Returns:
            A tensor of token ids of the same shape.
        """
        return self.vocab_ids_to_adj_matrix_ids_map.lookup(tokens)

    def forward_keyword_predictor(self, context_ids, context_length):
    # def forward_keyword_predictor(self, context_ids, context_length, keywords_mask_ids):
        with tf.variable_scope(name_or_scope=self.kp_scope_name, reuse=tf.AUTO_REUSE):
            context_embed = self.embedder(context_ids)
            context_code = self.context_encoder(context_embed, sequence_length=context_length)[1]
            keyword_score = self.prev_predict_layer(context_code)
            keep_rate = tf.cond(tf.equal(tx.global_mode(), tf.estimator.ModeKeys.TRAIN), lambda: self.drop_rate, lambda:1.0)
            keyword_score = tf.nn.dropout(keyword_score, keep_rate)
            keyword_score = self.predict_layer(keyword_score)
            # keywords_mask = tf.map_fn(lambda x: tf.sparse_to_dense(x, [self.kw_vocab.size], 1., -1e8, False),
            #                           keywords_mask_ids, dtype=tf.float32, parallel_iterations=True)[:, 4:]
            keywords_mask = tf.map_fn(self.generate_keyword_mask, context_ids, dtype=tf.float32, parallel_iterations=True)
            keyword_score = keyword_score - 1 + keywords_mask
            return keyword_score

    def compute_loss_and_acc(self, batch):
        # keywords_mask_ids = self.kw_vocab.map_tokens_to_ids(batch['mask_text'])
        # predicted_keyword_score = self.forward_keyword_predictor(batch['context_text_ids'], batch['context_length'], keywords_mask_ids)
        predicted_keyword_score = self.forward_keyword_predictor(batch['context_text_ids'], batch['context_length'])

        label_keywords_ids = self.kw_vocab.map_tokens_to_ids(batch['keywords_text'])
        kw_labels = tf.map_fn(lambda x: tf.sparse_to_dense(x, [self.kw_vocab.size], 1., 0., False),
                              label_keywords_ids, dtype=tf.float32, parallel_iterations=True)[:, 4:]

        loss = tf.nn.sigmoid_cross_entropy_with_logits(labels=kw_labels, logits=predicted_keyword_score)
        loss = tf.reduce_mean(loss)
        kw_ans = tf.arg_max(predicted_keyword_score, -1)
        acc_label = tf.map_fn(lambda x: tf.gather(x[0], x[1]), (kw_labels, kw_ans), dtype=tf.float32)
        acc = tf.reduce_mean(acc_label)
        kws = tf.nn.top_k(predicted_keyword_score, k=5)[1]
        kws = tf.reshape(kws,[-1])
        kws = tf.map_fn(lambda x: self.kw_list[x], kws, dtype=tf.int64)
        kws = tf.reshape(kws,[-1, 5])
        return loss, acc, kws #, predicted_keyword_score

    def predict_keywords(self, batch):
        return self.compute_loss_and_acc(batch)

    def train_keywords(self):
        batch = self.iterator.get_next()
        loss, acc, kws, = self.compute_loss_and_acc(batch)
        op_step = tf.Variable(0, name='op_step')
        train_op = tx.core.get_train_op(loss, global_step=op_step, hparams=self.model_config._kp_opt_hparams)
        max_val_acc = 0.
        self.saver = tf.train.Saver()
        with tf.Session(config=self.gpu_config) as sess:
            sess.run(tf.global_variables_initializer())
            sess.run(tf.local_variables_initializer())
            sess.run(tf.tables_initializer())
            for epoch_id in range(self.model_config._max_epoch):
                self.iterator.switch_to_train_data(sess)
                cur_step = 0
                cnt_acc = []
                while True:
                    try:
                        cur_step += 1
                        feed = {tx.global_mode(): tf.estimator.ModeKeys.TRAIN}
                        loss_, acc_ = sess.run([train_op, acc], feed_dict=feed)
                        cnt_acc.append(acc_)
                        if cur_step % 200 == 0:
                            logs_loss_acc = 'batch {}, loss={}, acc1={}'.format(cur_step, loss_, np.mean(cnt_acc[-200:]))
                            add_log(self.logs_save_path, logs_loss_acc)
                    except tf.errors.OutOfRangeError:
                        break
                self.iterator.switch_to_val_data(sess)
                cnt_acc = []
                while True:
                    try:
                        feed = {tx.global_mode(): tf.estimator.ModeKeys.EVAL}
                        acc_ = sess.run(acc, feed_dict=feed)
                        cnt_acc.append(acc_)
                    except tf.errors.OutOfRangeError:
                        mean_acc = np.mean(cnt_acc)
                        # if mean_acc > max_val_acc:
                        #     max_val_acc = mean_acc
                        #     self.saver.save(sess, self.model_config._kp_save_path)
                        logs_loss_acc = 'epoch_id {}, valid acc1={}'.format(epoch_id+1, mean_acc)
                        add_log(self.logs_save_path, logs_loss_acc)
                        break

                self.iterator.switch_to_test_data(sess)
                # cnt_acc = []
                cnt_acc, cnt_rec1, cnt_rec3, cnt_rec5 = [], [], [], []
                while True:
                    try:
                        # feed = {tx.global_mode(): tf.estimator.ModeKeys.EVAL}
                        feed = {tx.global_mode(): tf.estimator.ModeKeys.PREDICT}
                        # acc_ = sess.run(acc, feed_dict=feed)
                        acc_, kw_ans, kw_labels = sess.run([acc, kws, batch['keywords_text_ids']], feed_dict=feed)
                        cnt_acc.append(acc_)
                        rec = [0,0,0,0,0]
                        sum_kws = 0
                        for i in range(len(kw_ans)):
                            sum_kws += sum(kw_labels[i] > 3)
                            for j in range(5):
                                if kw_ans[i][j] in kw_labels[i]:
                                    for k in range(j, 5):
                                        rec[k] += 1
                        cnt_rec1.append(rec[0]/sum_kws)
                        cnt_rec3.append(rec[2]/sum_kws)
                        cnt_rec5.append(rec[4]/sum_kws)
                    except tf.errors.OutOfRangeError:
                        mean_acc = np.mean(cnt_acc)
                        if mean_acc > max_val_acc:
                            max_val_acc = mean_acc
                            self.saver.save(sess, self.model_config._kp_save_path)
                        logs_loss_acc = 'epoch_id {}, test acc1={}'.format(epoch_id+1, mean_acc)
                        add_log(self.logs_save_path, logs_loss_acc)
                        logs_loss_acc = 'test_kw acc@1={:.4f}, rec@1={:.4f}, rec@3={:.4f}, rec@5={:.4f}'.format(
                            np.mean(cnt_acc), np.mean(cnt_rec1), np.mean(cnt_rec3), np.mean(cnt_rec5))
                        add_log(self.logs_save_path, logs_loss_acc)
                        # self._logging(logs_loss_acc)
                        # print('epoch_id {}, valid acc1={}'.format(epoch_id+1, mean_acc))
                        break

    def test_keywords(self):
        batch = self.iterator.get_next()
        loss, acc, kws = self.compute_loss_and_acc(batch)
        saver = tf.train.Saver()
        with tf.Session(config=self.gpu_config) as sess:
            sess.run(tf.global_variables_initializer())
            sess.run(tf.local_variables_initializer())
            sess.run(tf.tables_initializer())
            saver.restore(sess, self.model_config._kp_save_path)
            self.iterator.switch_to_test_data(sess)
            cnt_acc, cnt_rec1, cnt_rec3, cnt_rec5 = [], [], [], []
            while True:
                try:
                    feed = {tx.global_mode(): tf.estimator.ModeKeys.PREDICT}
                    acc_, kw_ans, kw_labels = sess.run([acc, kws, batch['keywords_text_ids']], feed_dict=feed)
                    cnt_acc.append(acc_)
                    rec = [0,0,0,0,0]
                    sum_kws = 0
                    for i in range(len(kw_ans)):
                        sum_kws += sum(kw_labels[i] > 3)
                        for j in range(5):
                            if kw_ans[i][j] in kw_labels[i]:
                                for k in range(j, 5):
                                    rec[k] += 1
                    cnt_rec1.append(rec[0]/sum_kws)
                    cnt_rec3.append(rec[2]/sum_kws)
                    cnt_rec5.append(rec[4]/sum_kws)

                except tf.errors.OutOfRangeError:
                    logs_loss_acc = 'test_kw acc@1={:.4f}, rec@1={:.4f}, rec@3={:.4f}, rec@5={:.4f}'.format(
                        np.mean(cnt_acc), np.mean(cnt_rec1), np.mean(cnt_rec3), np.mean(cnt_rec5))
                    print(logs_loss_acc)
                    # add_log(self.logs_save_path, logs_loss_acc)
                    break

    def forward_response_retrieval(self, batch):
        predicted_keyword_score = self.forward_keyword_predictor(batch['context_text_ids'], batch['context_length'])

        kw_weight, predict_kw = tf.nn.top_k(predicted_keyword_score, k=3)
        predict_kw = tf.reshape(predict_kw, [-1])
        predict_kw = tf.map_fn(lambda x: self.kw_list[x], predict_kw, dtype=tf.int64)
        predict_kw = tf.reshape(predict_kw, [-1, 3])
        with tf.variable_scope(name_or_scope=self.rr_scope_name, reuse=tf.AUTO_REUSE):
            embed_code = self.embedder(predict_kw)
            embed_code = tf.reduce_sum(embed_code, axis=1)
            embed_code = self.linear_transform(embed_code)

            source_embed = self.embedder(batch['source_text_ids'])
            target_embed = self.embedder(batch['target_text_ids'])
            target_embed = tf.reshape(target_embed, [-1, self.data_config._max_seq_len + 2, self.embedder.dim])
            target_length = tf.reshape(batch['target_length'], [-1])
            source_code = self.source_encoder(
                source_embed,
                sequence_length_minor=batch['source_length'],
                sequence_length_major=batch['source_utterance_cnt'])[1]  #
            target_code = self.target_encoder(
                target_embed,
                sequence_length=target_length)[1]
            target_kwcode = self.target_kwencoder(
                target_embed,
                sequence_length=target_length)[1]
            target_code = tf.concat([target_code[0], target_code[1], target_kwcode[0], target_kwcode[1]], -1)
            target_code = tf.reshape(target_code, [-1, 20, self.model_config._code_len])

            source_code = tf.concat([source_code, embed_code], -1)
            source_code = tf.expand_dims(source_code, 1)
            source_code = tf.tile(source_code, [1, 20, 1])
            feature_code = target_code * source_code
            feature_code = tf.reshape(feature_code, [-1, self.model_config._code_len])

            logits = self.linear_matcher(feature_code)
            logits = tf.reshape(logits, [-1, 20])
            labels = tf.one_hot(batch['label'], 20)
            loss = tf.reduce_mean(tf.nn.sigmoid_cross_entropy_with_logits(labels=labels, logits=logits))
            ans = tf.arg_max(logits, -1)
            acc = tx.evals.accuracy(batch['label'], ans)
            rank = tf.nn.top_k(logits, k=20)[1]
            return loss, acc, rank

    def train(self):
        batch = self.iterator.get_next()
        kw_loss, kw_acc, _ = self.predict_keywords(batch)
        kw_saver = tf.train.Saver()
        loss, acc, rank = self.forward_response_retrieval(batch)
        op_step = tf.Variable(0, name='retrieval_step')
        train_op = tx.core.get_train_op(loss, global_step=op_step, hparams=self.model_config._retrieval_opt_hparams)
        max_val_acc = 0.
        with tf.Session(config=self.gpu_config) as sess:
            sess.run(tf.tables_initializer())
            sess.run(tf.global_variables_initializer())
            sess.run(tf.local_variables_initializer())
            kw_saver.restore(sess, self.model_config._kp_save_path)
            saver = tf.train.Saver()
            #saver.restore(sess, self.model_config._retrieval_save_path)
            for epoch_id in range(self.model_config._max_epoch):
                self.iterator.switch_to_train_data(sess)
                cur_step = 0
                cnt_acc = []
                while True:
                    try:
                        cur_step += 1
                        feed = {tx.global_mode(): tf.estimator.ModeKeys.TRAIN}
                        loss, acc_ = sess.run([train_op, acc], feed_dict=feed)
                        cnt_acc.append(acc_)
                        if cur_step % 200 == 0:
                            logs_loss_acc = 'batch {}, loss={}, acc1={}'.format(cur_step, loss, np.mean(cnt_acc[-200:]))
                            add_log(self.logs_save_path, logs_loss_acc)
                    except tf.errors.OutOfRangeError:
                        break

                self.iterator.switch_to_val_data(sess)
                cnt_acc, cnt_kwacc = [], []
                while True:
                    try:
                        feed = {tx.global_mode(): tf.estimator.ModeKeys.EVAL}
                        acc_, kw_acc_ = sess.run([acc, kw_acc], feed_dict=feed)
                        cnt_acc.append(acc_)
                        cnt_kwacc.append(kw_acc_)
                    except tf.errors.OutOfRangeError:
                        mean_acc = np.mean(cnt_acc)
                        logs_loss_acc = 'epoch_id {}, valid acc1={}, kw_acc1={}'.format(epoch_id+1, mean_acc, np.mean(cnt_kwacc))
                        add_log(self.logs_save_path, logs_loss_acc)
                        # if mean_acc > max_val_acc:
                        #     max_val_acc = mean_acc
                        #     saver.save(sess, self.model_config._retrieval_save_path)
                        break

                self.iterator.switch_to_test_data(sess)
                rank_cnt = []
                cnt_acc, cnt_kwacc = [], []
                while True:
                    try:
                        feed = {tx.global_mode(): tf.estimator.ModeKeys.PREDICT}
                        acc_, kw_acc_, ranks, labels = sess.run([acc, kw_acc, rank, batch['label']], feed_dict=feed)
                        for i in range(len(ranks)):
                            rank_cnt.append(np.where(ranks[i]==labels[i])[0][0])
                        cnt_acc.append(acc_)
                        cnt_kwacc.append(kw_acc_)
                    except tf.errors.OutOfRangeError:
                        mean_acc = np.mean(cnt_acc)
                        mean_kwacc = np.mean(cnt_kwacc)
                        rec = [0,0,0,0,0]
                        MRR = 0
                        for rank_ in rank_cnt:
                            for i in range(5):
                                rec[i] += (rank_ <= i)
                            MRR += 1 / (rank_+1)
                        logs_loss_acc = 'epoch_id {} test acc1={} kw_acc1={} rec1@20={:.4f}, rec3@20={:.4f}, rec5@20={:.4f}, MRR={:.4f}'.format(
                            epoch_id+1, mean_acc, mean_kwacc, rec[0]/len(rank_cnt), rec[2]/len(rank_cnt), rec[4]/len(rank_cnt), MRR/len(rank_cnt))
                        add_log(self.logs_save_path, logs_loss_acc)
                        if mean_acc > max_val_acc:
                            max_val_acc = mean_acc
                            saver.save(sess, self.model_config._retrieval_save_path)
                        break

    def test(self):
        batch = self.iterator.get_next()
        loss, acc, rank = self.forward_response_retrieval(batch)
        with tf.Session(config=self.gpu_config) as sess:
            sess.run(tf.tables_initializer())
            self.saver = tf.train.Saver()
            self.saver.restore(sess, self.model_config._retrieval_save_path)
            self.iterator.switch_to_test_data(sess)
            rank_cnt = []
            while True:
                try:
                    feed = {tx.global_mode(): tf.estimator.ModeKeys.PREDICT}
                    ranks, labels = sess.run([rank, batch['label']], feed_dict=feed)
                    for i in range(len(ranks)):
                        rank_cnt.append(np.where(ranks[i]==labels[i])[0][0])
                except tf.errors.OutOfRangeError:
                    rec = [0,0,0,0,0]
                    MRR = 0
                    for rank in rank_cnt:
                        for i in range(5):
                            rec[i] += (rank <= i)
                        MRR += 1 / (rank+1)
                    print('test rec1@20={:.4f}, rec3@20={:.4f}, rec5@20={:.4f}, MRR={:.4f}'.format(
                        rec[0]/len(rank_cnt), rec[2]/len(rank_cnt), rec[4]/len(rank_cnt), MRR/len(rank_cnt)))
                    break

    def retrieve_init(self, sess):
        data_batch = self.iterator.get_next()
        loss, acc, _ = self.forward_retrieval(data_batch)
        self.corpus = self.data_config._corpus
        self.corpus_data = tx.data.MonoTextData(self.data_config.corpus_hparams)
        corpus_iterator = tx.data.DataIterator(self.corpus_data)
        batch = corpus_iterator.get_next()
        corpus_embed = self.embedder(batch['corpus_text_ids'])
        utter_code = self.target_encoder(corpus_embed, sequence_length=batch['corpus_length'])[1]
        utter_kwcode = self.target_kwencoder(corpus_embed, sequence_length=batch['corpus_length'])[1]
        utter_code = tf.concat([utter_code[0], utter_code[1], utter_kwcode[0], utter_kwcode[1]], -1)
        self.corpus_code = np.zeros([0, self.model_config._code_len])

        corpus_iterator.switch_to_dataset(sess)
        sess.run(tf.tables_initializer())
        saver = tf.train.Saver()
        saver.restore(sess, self.model_config._retrieval_save_path)
        feed = {tx.global_mode(): tf.estimator.ModeKeys.PREDICT}
        while True:
            try:
                utter_code_ = sess.run(utter_code, feed_dict=feed)
                self.corpus_code = np.concatenate([self.corpus_code, utter_code_], axis=0)
            except tf.errors.OutOfRangeError:
                break
        self.keywords_embed = tf.nn.l2_normalize(self.embedder(self.kw_list), axis=1)
        self.kw_embedding = sess.run(self.keywords_embed)

        # predict keyword
        self.context_input = tf.placeholder(dtype=object, shape=(20))
        self.context_length_input = tf.placeholder(dtype=tf.int32, shape=(1))
        context_ids = tf.expand_dims(self.vocab.map_tokens_to_ids(self.context_input), 0)
        context_embed = self.embedder(context_ids)
        context_code = self.context_encoder(context_embed, sequence_length=self.context_length_input)[1]
        context_code = self.prev_predict_layer(context_code)
        matching_score = self.predict_layer(context_code)
        tf.add_to_collection('prev_pred_kw_id', tf.cast([-1e8], tf.float32))
        prev_pred_kw_id = tf.get_collection('prev_pred_kw_id')[-1]
        context_related_mask, target_guide_mask = tf.map_fn(lambda x: self.generate_keyword_mask(x[0], x[1]),
                                                            (context_ids, prev_pred_kw_id),
                                                            dtype=(tf.float32, tf.float32), parallel_iterations=True)
        tf.get_default_graph().clear_collection('prev_pred_kw_id')
        matching_score = matching_score - 1 + context_related_mask
        self.candi_output =tf.nn.top_k(tf.squeeze(matching_score, 0), self.data_config._keywords_num)[1]

        # retrieve
        self.minor_length_input = tf.placeholder(dtype=tf.int32, shape=(1, 9))
        self.major_length_input = tf.placeholder(dtype=tf.int32, shape=(1))
        self.history_input = tf.placeholder(dtype=object, shape=(9, self.data_config._max_seq_len + 2))
        self.kw_input = tf.placeholder(dtype=tf.int32)
        history_ids = self.vocab.map_tokens_to_ids(self.history_input)
        history_embed = self.embedder(history_ids)
        history_code = self.source_encoder(tf.expand_dims(history_embed, axis=0),
                                           sequence_length_minor=self.minor_length_input,
                                           sequence_length_major=self.major_length_input)[1]
        self.next_kw_ids = self.kw_list[self.kw_input]
        embed_code = tf.expand_dims(self.embedder(self.next_kw_ids), 0)
        embed_code = self.linear_transform(embed_code)
        history_code = tf.concat([history_code, embed_code], 1)
        select_corpus = tf.cast(self.corpus_code, dtype=tf.float32)
        feature_code = self.linear_matcher(select_corpus * history_code)
        self.ans_output = tf.nn.top_k(tf.squeeze(feature_code,1), k=self.data_config._agent_retrieval_candidates)[1]

    def retrieve(self, history_all, sess):
        history, seq_len, turns, context, context_len = history_all
        kw_candi = sess.run(self.candi_output, feed_dict={self.context_input: context,
                                                          self.context_length_input: [context_len]})
        for kw in kw_candi:
            tmp_score = sum(self.kw_embedding[kw] * self.kw_embedding[self.data_config._keywords_dict[self.target]])
            if tmp_score > self.score:
                self.score = tmp_score
                self.next_kw = self.data_config._keywords_candi[kw]
                break
        ans = sess.run(self.ans_output, feed_dict={self.history_input: history,
                                                   self.minor_length_input: [seq_len], self.major_length_input: [turns],
                                                   self.kw_input: self.data_config._keywords_dict[self.next_kw]})
        flag = 0
        reply = self.corpus[ans[0]]
        #self.reply_list = []
        for i in ans:
            if i in self.reply_list:  # avoid repeat
                continue
            for wd in kw_tokenize(self.corpus[i]):
                if wd in self.data_config._keywords_candi:
                    tmp_score = sum(self.kw_embedding[self.data_config._keywords_dict[wd]] *
                                    self.kw_embedding[self.data_config._keywords_dict[self.target]])
                    # if tmp_score >= self.score:
                    #     self.reply_list.append(i)
                    #     reply = self.corpus[i]
                    #     self.score = tmp_score
                    #     self.next_kw = wd
                    #     flag = 1
                    #     break
                    if tmp_score > self.score and self.score < 1.0:
                        self.reply_list.append(i)
                        reply = self.corpus[i]
                        self.score = tmp_score
                        self.refined_next_kw = wd
                        flag = 1
                        break
                    else:
                        if wd == self.target:
                            self.reply_list.append(i)
                            reply = self.corpus[i]
                            self.score = tmp_score
                            self.refined_next_kw = wd
                            flag = 1
                            break
            if flag == 0:
                continue
            break
        return reply
