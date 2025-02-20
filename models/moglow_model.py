import torch
from torch import nn
from models import BaseModel
from .util.generation import autoregressive_generation_multimodal
from .moglow.models import Glow
import torch.nn.functional as F

class MoglowModel(BaseModel):
    def __init__(self, opt):
        super().__init__(opt)

        input_seq_lens = self.input_seq_lens
        dins = self.dins
        douts = self.douts

        # import pdb;pdb.set_trace()
        cond_dim = sum(map(lambda x: x[0]*x[1],zip(dins,input_seq_lens)))
        self.output_mod_glows = []
        for i, mod in enumerate(self.output_mods):
            if i == 0:
                length = self.output_lengths[i]
            else:
                assert self.output_lenghts[i] == length
            output_dim = douts[i]
            self.network_model = self.opt.network_model
            if self.opt.use_projection:
                projection = nn.Linear(cond_dim,self.opt.cond_dim)
                cond_dim = self.opt.cond_dim
                setattr(self, "net"+"_projection", projection)
            glow = Glow(output_dim, cond_dim, self.opt, self.output_lengths[i])
            name = "_output_glow_"+mod.replace(".","_")
            setattr(self, "net"+name, glow)
            self.output_mod_glows.append(glow)

        self.inputs = []
        self.targets = []
        self.criterion = nn.MSELoss()
        # self.has_initialized = False

    def parse_base_arguments(self):
        super().parse_base_arguments()
        self.input_seq_lens = [int(x) for x in str(self.opt.input_seq_lens).split(",")]
        self.output_seq_lens = [int(x) for x in str(self.opt.output_seq_lens).split(",")]
        if self.opt.phase == "inference" and self.opt.network_model == "LSTM":
            self.input_lengths = self.input_seq_lens
            self.output_lengths = self.output_seq_lens
        #else:
        #    self.input_lengths = [int(x) for x in self.input_lengths.split(",")]
        #    self.output_lengths = [int(x) for x in self.output_lengths.split(",")]

        if len(self.output_time_offsets) < len(self.output_mods):
            if len(self.output_time_offsets) == 1:
                self.output_time_offsets = self.output_time_offsets*len(self.output_mods)
            else:
                raise Exception("number of output_time_offsets doesnt match number of output_mods")

        if len(self.input_time_offsets) < len(self.input_mods):
            if len(input_time_offsets) == 1:
                self.input_time_offsets = self.input_time_offsets*len(self.input_mods)
            else:
                raise Exception("number of input_time_offsets doesnt match number of input_mods")

    def name(self):
        return "Moglow"

    @staticmethod
    def modify_commandline_options(parser, opt):
        parser.add_argument('--dhid', type=int, default=512)
        parser.add_argument('--input_seq_lens', type=str, default="10,11")
        parser.add_argument('--output_seq_lens', type=str, default="1")
        parser.add_argument('--glow_K', type=int, default=16)
        parser.add_argument('--actnorm_scale', type=float, default=1.0)
        parser.add_argument('--flow_permutation', type=str, default="invconv")
        parser.add_argument('--flow_dist', type=str, default="normal")
        parser.add_argument('--flow_dist_param', type=int, default=50)
        parser.add_argument('--flow_coupling', type=str, default="affine")
        parser.add_argument('--flow_coupling_dmodel', type=int, default=400)
        parser.add_argument('--flow_coupling_nheads', type=int, default=10)
        parser.add_argument('--num_layers', type=int, default=2)
        parser.add_argument('--network_model', type=str, default="LSTM")
        parser.add_argument('--use_projection', action='store_true')
        parser.add_argument('--cond_dim', type=int, default=256)
        parser.add_argument('--dropout', type=float, default=0.1)
        parser.add_argument('--LU_decomposed', action='store_true')
        return parser

    def forward(self, data, temp=1.0, zss=None):
        eps_std = temp
        # import pdb;pdb.set_trace()
        data2 = []
        for i,mod in enumerate(self.input_mods):
            input_ = data[i]
            print(input_.shape)
            if len(input_.shape) == 3:
                input_ = input_.permute(1,0,2)
            elif len(input_.shape) == 2:
                input_ = input_.permute(1,0)
            if self.input_seq_lens[i] > 1:
                if self.input_proc_types[i]=="single":
                    input_ = self.concat_sequence(self.input_seq_lens[i], input_, repeat_num=self.output_lengths[0])
                else:
                    input_ = self.concat_sequence(self.input_seq_lens[i], input_)
                input_ = input_.permute(0,2,1)
            else:
                input_ = input_.permute(0,2,1)
            #data[i] = input_
            #print(input_.shape)
            data2.append(input_)
        cond = torch.cat(data2, dim=1)
        if self.opt.use_projection:
            cond = self.net_projection(cond.permute(0,2,1)).permute(0,2,1)
        #import pdb;pdb.set_trace()
        if self.opt.network_model == "transformer":
            compute_zs = zss is None
            zss_new = []
            output_index = cond.shape[2]-1
            outputs = []
            for i, mod in enumerate(self.output_mods):
                if cond.shape[2] < self.output_lengths[i]:
                    cond = F.pad(cond,(0,self.output_lengths[i]-cond.shape[2]),'constant',0)
                    #print(cond.shape)
                z_new = self.output_mod_glows[i].distribution.sample(self.output_mod_glows[i].z_shape, eps_std=eps_std, device=cond.device)
                if compute_zs:
                    #print(output_index)
                    #print(self.output_lengths[0])
                    #print(data[output_index_in_input])
                    output_index_in_input = self.input_mods.index(mod)
                    prev_outs = data[output_index_in_input][-(self.output_lengths[i]-1):].permute(1,2,0)
                    #prev_outs = torch.cat([prev_outs,prev_outs[:,:,-1:]],dim=-1)
                    #print(prev_outs.shape)
                    prev_outs = F.pad(prev_outs,(0,self.output_lengths[i]-prev_outs.shape[2]),'constant',0)
                    #print(prev_outs.shape)
                    z, nll = self.output_mod_glows[i](x=prev_outs, cond=cond)
                    z[:,:,output_index:output_index+1] = z_new
                else:
                    z = torch.cat([zss[i][:,:,-(self.output_lengths[i]-1):],z_new],dim=-1)
                    if z.shape[2] < self.output_lengths[i]:
                        z = F.pad(z,(0,self.output_lengths[i]-z.shape[2]),'constant',0)

                # import pdb;pdb.set_trace()
                output = self.output_mod_glows[i](z=z, cond=cond, eps_std=eps_std, reverse=True)
                output = output[:,:,output_index:output_index+1]
                outputs.append(output.permute(0,2,1))
                # import pdb;pdb.set_trace()
                zss_new.append(z[:,:,:output_index+1])
            zss = zss_new
            return outputs, zss
        else:
            outputs = []
            for i, mod in enumerate(self.output_mods):
                output = self.output_mod_glows[i](z=None, cond=cond, eps_std=eps_std, reverse=True)
                outputs.append(output.permute(0,2,1))
            return outputs

    def generate(self,features, teacher_forcing=False, ground_truth=False, sequence_length=None, use_temperature=False, temperature=1.0, save_jit=False, save_jit_path=""):
        if self.network_model=="LSTM":
            for glow in self.output_mod_glows:
                glow.init_lstm_hidden()
            keep_latents = False
        else:
            keep_latents = True
        if self.opt.network_model=="transformer":
            output_seq = autoregressive_generation_multimodal(features, self, autoreg_mods=self.output_mods, teacher_forcing=teacher_forcing, ground_truth=ground_truth, keep_latents=keep_latents,seed_lengths=self.input_seq_lens, sequence_length=sequence_length, use_temperature=use_temperature, temperature=temperature, save_jit=save_jit, save_jit_path=save_jit_path)
        else:
            output_seq = autoregressive_generation_multimodal(features, self, autoreg_mods=self.output_mods, teacher_forcing=teacher_forcing, ground_truth=ground_truth, keep_latents=keep_latents, sequence_length=sequence_length, use_temperature=use_temperature, temperature=temperature, save_jit=save_jit, save_jit_path=save_jit_path)
        return output_seq

    def on_test_start(self):
        if self.network_model=="LSTM":
            for glow in self.output_mod_glows:
                glow.init_lstm_hidden()

    def on_train_start(self):
        if self.network_model=="LSTM":
            for glow in self.output_mod_glows:
                glow.init_lstm_hidden()

    def on_test_batch_start(self, batch, batch_idx, dataloader_idx):
        if self.network_model=="LSTM":
            for glow in self.output_mod_glows:
                glow.init_lstm_hidden()

    def on_train_batch_start(self, batch, batch_idx, dataloader_idx):
        if self.network_model=="LSTM":
            # self.zero_grad()
            for glow in self.output_mod_glows:
                glow.init_lstm_hidden()

    def concat_sequence(self, seqlen, data, repeat_num=1):
        #NOTE: this could be done as preprocessing on the dataset to make it a bit more efficient, but we are only going to
        # use this for baseline moglow, so I thought it wasn't worth it to put it there.
        """
        Concatenates a sequence of features to one.
        """
        nn,n_timesteps,n_feats = data.shape
        L = n_timesteps-(seqlen-1)
        # import pdb;pdb.set_trace()
        inds = torch.zeros((L, seqlen), dtype=torch.long)

        #create indices for the sequences we want
        rng = torch.arange(0, n_timesteps, dtype=torch.long)
        for ii in range(0,seqlen):
            # print(rng[ii:(n_timesteps-(seqlen-ii-1))].shape)
            # inds[:, ii] = torch.transpose(rng[ii:(n_timesteps-(seqlen-ii-1))], 0, 1)
            inds[:, ii] = rng[ii:(n_timesteps-(seqlen-ii-1))]

        #slice each sample into L sequences and store as new samples
        cc=data[:,inds,:].clone()

        #print ("cc: " + str(cc.shape))

        #reshape all timesteps and features into one dimention per sample
        dd = cc.reshape((nn, L, seqlen*n_feats))
        dd = torch.tile(dd, (1,repeat_num,1))
        #print ("dd: " + str(dd.shape))
        return dd

    def set_inputs(self, data):
        self.inputs = []
        self.targets = []
        for i, mod in enumerate(self.input_mods):
            input_ = data["in_"+mod]
            if self.input_seq_lens[i] > 1:
                # input_ = input_.permute(0,2,1)
                if self.input_proc_types[i]=="single":
                    input_ = self.concat_sequence(self.input_seq_lens[i], input_, repeat_num=self.output_lengths[0])
                else:
                    input_ = self.concat_sequence(self.input_seq_lens[i], input_)
                input_ = input_.permute(0,2,1)
            else:
                #print(input_.shape)
                input_ = input_.permute(0,2,1)
            self.inputs.append(input_)
        for i, mod in enumerate(self.output_mods):
            target_ = data["out_"+mod]
            if self.output_seq_lens[i] > 1:
                # target_ = target_.permute(0,2,1)
                target_ = self.concat_sequence(self.output_seq_lens[i], target_)
                target_ = target_.permute(0,2,1)
            else:
                target_ = target_.permute(0,2,1)
            # target_ = target_.permute(2,0,1)
            self.targets.append(target_)

    def training_step(self, batch, batch_idx):
        self.set_inputs(batch)
        # import pdb;pdb.set_trace()
        #for inp in self.inputs:
        #    print(inp.shape)
        cond = torch.cat(self.inputs, dim=1)
        if self.opt.use_projection:
            cond = self.net_projection(cond.permute(0,2,1)).permute(0,2,1)
        loss=0
        for i, mod in enumerate(self.output_mods):
            z, nll = self.output_mod_glows[i](x=self.targets[i], cond=cond)
            nll_loss = Glow.loss_generative(nll)
            loss += nll_loss

        # output = self.net_glow(z=None, cond=torch.cat(self.inputs, dim=1), eps_std=1.0, reverse=True, output_length=self.output_lengths[0])

        # mse_loss = self.criterion(output, self.targets[0])
        # loss = 0.1*nll_loss + 100*mse_loss
        # loss = mse_loss
        # print(nll_loss)
        # print(mse_loss)
        self.log('nll_loss', nll_loss)
        self.log('loss', loss)
        # self.log('mse_loss', mse_loss)
        # import pdb;pdb.set_trace()
        # if not self.has_initialized:
        #     self.has_initialized=True
        #     return torch.tensor(0.0, dtype=torch.float32, requires_grad=True)
        # else:
        # print(loss)
        return loss
        # return torch.tensor(0.0, dtype=torch.float32, requires_grad=True)

    #to help debug XLA stuff, like missing ops, or data loading/compiling bottlenecks
    # see https://youtu.be/iwtpwQRdb3Y?t=1056
    #def on_epoch_end(self):
    #    xm.master_print(met.metrics_report())


    #def optimizer_step(self, epoch, batch_idx, optimizer, optimizer_idx,
    #                           optimizer_closure, on_tpu, using_native_amp, using_lbfgs):
    #    optimizer.zero_grad()
