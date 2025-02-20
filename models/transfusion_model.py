import torch
from .transformer import BasicTransformerModel
from models import BaseModel
from models.flowplusplus import FlowPlusPlus
#from models.flowplusplus import FlowPlusPlus2 as FlowPlusPlus
import ast
from torch import nn
import torch.nn.functional as F

from .util.generation import autoregressive_generation_multimodal

from torch.distributions.distribution import Distribution
import numpy as np

from diffusion.models import DiT
# from diffusion.models_old import DiT

from diffusion.gaussian_diffusion import LossType, ModelMeanType, ModelVarType
from diffusion.gaussian_diffusion import GaussianDiffusion, get_named_beta_schedule

# from diffusion.gaussian_diffusion_old import LossType, ModelMeanType, ModelVarType
# from diffusion.gaussian_diffusion_old import GaussianDiffusion, get_named_beta_schedule

import time

class TransfusionModel(BaseModel):
    def __init__(self, opt, **kwargs):
        super().__init__(opt, **kwargs)
        input_mods = self.input_mods
        output_mods = self.output_mods
        dins = self.dins
        douts = self.douts
        input_lengths = self.input_lengths
        output_lengths = self.output_lengths
        if self.opt.conditioning_seq_lens is not None:
            self.conditioning_seq_lens = [int(x) for x in str(self.opt.conditioning_seq_lens).split(",")]
        else:
            self.conditioning_seq_lens = [int(x) for x in str(self.opt.output_lengths).split(",")]

        self.use_shared_crossmodal_encoder = self.opt.use_shared_crossmodal_encoder

        if self.opt.input_mod_masks is not None:
            self.input_mod_masks = [tuple((int(x) for x in y.split(" "))) for y in self.opt.input_mod_masks.split(",")]
            assert len(self.input_mod_masks) == len(output_mods)
            for mask in self.input_mod_masks:
                assert len(mask) == len(input_mods)
        else:
            self.input_mod_masks = [tuple((1 for x in input_mods)) for y in output_mods]

        self.input_mod_nets = []
        self.output_mod_nets = []
        self.output_mod_mean_nets = []
        self.output_mod_diffus = []
        self.module_names = []

        self.diffu_patch_size = opt.diffu_patch_size
        if isinstance(opt.diffu_patch_size,str):
            self.diffu_patch_size = ast.literal_eval(opt.diffu_patch_size)

        # print(type(self.diffu_patch_size))
        # print(ModelVarType[opt.diffu_model_var_type])

        self.gd = GaussianDiffusion(
            betas=get_named_beta_schedule(
                opt.diffu_beta_schedule,
                num_diffusion_timesteps=opt.num_diff_steps,
                scale_betas=opt.diffu_scale_betas,
            ),
            model_mean_type=ModelMeanType[opt.diffu_model_mean_type],
            model_var_type=ModelVarType[opt.diffu_model_var_type],
            loss_type=LossType[opt.diffu_loss_type],
        )

        self.diffusion = self.gd
        cond_dropout_probs = opt.cond_dropout_probs.split(",")
        if len(cond_dropout_probs) == 1:
            cond_dropout_probs = [float(cond_dropout_probs[0])]*len(self.output_mods)
        else:
            assert len(cond_dropout_probs) == len(self.output_mods)
            cond_dropout_probs = [float(p) for p in cond_dropout_probs]

        self.cond_dropout_probs = cond_dropout_probs

        self.num_diff_steps = opt.num_diff_steps
        #TODO: include option for discrete outputs
        for i, mod in enumerate(input_mods):
            net = BasicTransformerModel(opt.dhid, dins[i], opt.nhead, opt.dhid, 2, opt.dropout,
                                        ntokens=self.input_num_tokens[i],
                                        use_pos_emb=opt.use_pos_emb_inputs,
                                        use_rel_pos_emb=opt.use_rel_pos_emb_inputs,
                                        input_length=input_lengths[i],
                                        use_x_transformers=opt.use_x_transformers,
                                        opt=opt,
                                        discrete_inputs=self.input_types[i] == 'd')
            name = "_input_"+mod.replace(".","_")
            setattr(self,"net"+name, net)
            self.input_mod_nets.append(net)
            self.module_names.append(name)
        for i, mod in enumerate(output_mods):
            if not self.use_shared_crossmodal_encoder or i == 0:
                net = BasicTransformerModel(opt.dhid_diffu, opt.dhid, opt.nhead, opt.dhid, opt.nlayers, opt.dropout,
                                            ntokens=self.output_num_tokens[i], # tho not being used yet
                                            use_pos_emb=opt.use_pos_emb_output,
                                            use_rel_pos_emb=opt.use_rel_pos_emb_output,
                                            input_length=sum(input_lengths),
                                            use_x_transformers=opt.use_x_transformers,
                                            opt=opt)
                name = "_output_"+mod.replace(".","_")
                setattr(self, "net"+name, net)
                self.output_mod_nets.append(net)
                self.module_names.append(name)

            # import pdb;pdb.set_trace()
            #print(self.douts[i])
            diffu = DiT(layers=opt.diffu_depth,
                        hidden_dim=opt.dhid_diffu,
                        patch_dim=self.diffu_patch_size,
                        attention_heads=opt.diffu_num_heads,
                        input_dim=(self.output_lengths[i], self.douts[i]),
                        mlp_ratio=opt.diffu_mlp_ratio,
                        cond_dropout_prob=self.cond_dropout_probs[i],
                        sigma_learning=(ModelVarType[opt.diffu_model_var_type] in [ModelVarType.LEARNED, ModelVarType.LEARNED_RANGE]),
                        input_channels=1,
                        concat_cond=opt.diffu_concat_conds,
                        )
            name = "_output_diffu_"+mod.replace(".","_")
            setattr(self, "net"+name, diffu)
            self.output_mod_diffus.append(diffu)
            self.module_names.append(name)

        self.mean_loss = nn.MSELoss()
        #This is feature creep. Will remove soon
        # self.generate_full_masks()
        self.inputs = []
        self.targets = []
        self.mse_loss = 0
        self.nll_loss = 0

        self.prev_outputs = None

    def name(self):
        return "Transfusion"

    @staticmethod
    def modify_commandline_options(parser, opt):
        parser.add_argument('--dhid', type=int, default=512)
        parser.add_argument('--dhid_diffu', type=int, default=512)
        parser.add_argument('--conditioning_seq_lens', type=str, default=None, help="the number of outputs of the conditioning transformers to feed (meaning the number of elements along the sequence dimension)")
        parser.add_argument('--input_mod_masks', type=str, default=None, help="the inputs to feed to each mod, as a comma separated list of space sparated lists of 0s and 1s")
        parser.add_argument('--nlayers', type=int, default=6)
        parser.add_argument('--nhead', type=int, default=8)
        parser.add_argument('--diffu_num_heads', type=int, default=8)
        parser.add_argument('--diffu_patch_size', type=str, default="(1,2)")
        parser.add_argument('--dropout', type=float, default=0.1)
        parser.add_argument('--num_diff_steps', type=int, default=30)
        parser.add_argument('--num_sampling_steps', type=int, default=50)
        parser.add_argument('--diffu_depth', type=int, default=6)
        parser.add_argument('--diffu_mlp_ratio', type=float, default=4.0)
        parser.add_argument('--prev_outputs_factor', type=float, default=0.5, help="a factor determining how much of the previous output is used as starting point for next output")
        parser.add_argument('--cfg_scale', type=float, default=1.0, help="the cfg scale to use during inference")
        parser.add_argument('--cond_dropout_probs', type=str, default="0.1", help="the probabilities of dropping out the conditioning vector for each output, to be used for CFG")
        parser.add_argument('--diffu_model_var_type', type=str, default="LEARNED", help="the type of parametrization for the variance parameter of the diffusion head. One of LEARNED, FIXED_SMALL, FIXED_LARGE, LEARNED_RANGE")
        parser.add_argument('--diffu_model_mean_type', type=str, default="START_X", help="the type of parametrization for the mean parameter of the diffusion head. One of PREVIOUS_X, START_X, EPSILON")
        parser.add_argument('--diffu_loss_type', type=str, default="MSE", help="the type of loss for the diffusion model")
        # the beta_start and beta_end are not used atm.
        parser.add_argument('--diffu_beta_start', type=float, default=0.0001, help="the initial value for the diffusion weighting parameter")
        parser.add_argument('--diffu_beta_end', type=float, default=0.02, help="the final value for the diffusion weighting parameter")
        parser.add_argument('--diffu_scale_betas', type=float, default=1.0, help="the scale by which to multiply betas in diffusion")
        parser.add_argument('--diffu_beta_schedule', type=str, default="linear", help="the type of schedule by which to change the beta parameter")
        parser.add_argument('--use_shared_crossmodal_encoder', action='store_true', help="whether to feed different elements of the latent sequence of a single x-modal encoder to each output mod, or use a different encoder for each mod")
        parser.add_argument('--diffu_concat_conds', action='store_true', help="whehter to concatenate the conditioning vectors for DiT conditioning, on top of using Ada moluation")
        parser.add_argument('--use_rel_pos_emb_inputs', action='store_true', help="whether to use T5 relative positional embeddings for input modality transformers")
        parser.add_argument('--use_rel_pos_emb_output', action='store_true', help="whether to use T5 relative positional embeddings for output modality transformers")
        parser.add_argument('--use_pos_emb_inputs', action='store_true', help="whether to use positional embeddings for input modality transformers")
        parser.add_argument('--use_pos_emb_output', action='store_true', help="whether to use positional embeddings for output modality transformers")
        parser.add_argument('--use_pos_emb_coupling', action='store_true', help="whether to use positional embeddings for the coupling layer transformers")
        parser.add_argument('--use_rel_pos_emb_coupling', action='store_true', help="whether to use T5 relative positional embeddings for the coupling layer transformers")
        parser.add_argument('--use_rotary_pos_emb', action='store_true', help="whether to use rotary position embeddings")
        parser.add_argument('--use_x_transformers', action='store_true', help="whether to use rotary position embeddings")
        return parser

    def get_data_representations(self, data):
        latents = []
        for i, mod in enumerate(self.input_mods):
            if len(data[i].shape) == 2:
                this_data = data[i].unsqueeze(1) #assume we didn't have the batch dim
            else:
                this_data = data[i]
            if self.input_mod_nets[i].discrete_inputs: this_data = this_data.long()
            else: this_data = this_data.float()
            latents.append(self.input_mod_nets[i](this_data))
        return latents

    def get_latents(self, data, output_mods=None, latent_chunk_index=0):
        if output_mods is None:
            output_mods = self.output_mods
        latents1 = self.get_data_representations(data)
        latents = []
        all_latentss = {}
        cumsum = np.cumsum([0]+self.conditioning_seq_lens)
        for mod in output_mods:
            i = self.output_mods.index(mod)
            if self.input_mod_masks[i] not in all_latentss:
                latent1 = torch.cat([self.input_mod_masks[i][j]*l for j,l in enumerate(latents1)])
                if not self.use_shared_crossmodal_encoder:
                    all_latents = self.output_mod_nets[i](latent1)
                else:
                    all_latents = self.output_mod_nets[0](latent1)
                all_latentss[self.input_mod_masks[i]] = all_latents
            all_latents = all_latentss[self.input_mod_masks[i]]
            if not self.use_shared_crossmodal_encoder:
                trans_output = all_latents[latent_chunk_index*self.conditioning_seq_lens[i]:(latent_chunk_index+1)*self.conditioning_seq_lens[i]]
            else:
                assert latent_chunk_index == 0
                trans_output = all_latents[cumsum[i]:cumsum[i+1]]
            latents.append(trans_output.permute(1,0,2)) #time, batch, features -> batch, time, features

        return latents

    def get_dists(self, latents):
        dists = []
        for i,latent in enumerate(latents):
            dist = NormalizingFlow(self.output_mod_glows[i], latent)
            dists.append(dist)
        return dists

    def forward(self, data, temp=1.0, noises=None, output_mods=None, compute_logPs=True):
        # in lightning, forward defines the prediction/inference actions
        outputs = []
        if output_mods is None:
            output_mods = self.output_mods

        #Cross-modal transformers part
        start_time = time.time()
        latents = self.get_latents(data, output_mods)
        print("--- TF part: %s seconds ---" % (time.time() - start_time))

        #Diffusion part
        for j,mod in enumerate(output_mods):
            i = self.output_mods.index(mod)
            # noise = noises[i] if noises is not None else None
            diffu = self.output_mod_diffus[i]
            z1 = torch.randn(1, 1, self.output_lengths[i], self.douts[i], device=self.device)
            if self.opt.prev_outputs_factor > 0.0 and self.prev_outputs is not None:
                betas = get_named_beta_schedule(
                        self.opt.diffu_beta_schedule,
                        num_diffusion_timesteps=self.opt.num_diff_steps,
                        scale_betas=self.opt.diffu_scale_betas,
                    )
                #betas=betas[int(self.opt.num_diff_steps*(1.0-self.opt.prev_outputs_factor)):]
                betas=betas[:int(self.opt.num_diff_steps*(1.0-self.opt.prev_outputs_factor))]
                diffusion = GaussianDiffusion(
                    betas=betas,
                    model_mean_type=ModelMeanType[self.opt.diffu_model_mean_type],
                    model_var_type=ModelVarType[self.opt.diffu_model_var_type],
                    loss_type=LossType[self.opt.diffu_loss_type],
                )
                device = self.prev_outputs[j].device
                z = self.diffusion.q_sample(self.prev_outputs[j].unsqueeze(0),torch.tensor(len(betas)-1, device=device))
            else:
                diffusion = self.diffusion
                z = z1

            # latents[j] = latents[j][:,0,:]
            start_time = time.time()
            # Setup classifier-free guidance:
            if self.opt.cfg_scale != 1.0:
                z = torch.cat([z, z], 0)
                latents[j] = torch.cat([latents[j],torch.zeros_like(latents[j])],0)
                model_kwargs={"cond":latents[j], "cfg_scale": self.opt.cfg_scale}

                samples = diffusion.p_sample_loop(
                    diffu.forward_with_cfg, z.shape, z, clip_denoised=False, model_kwargs=model_kwargs, progress=False, device=self.device
                )
            else:
                model_kwargs={"cond":latents[j]}
                samples = diffusion.p_sample_loop(
                   diffu.forward, z.shape, z, clip_denoised=False, model_kwargs=model_kwargs, progress=False, device=self.device
                )
            print("--- Diffu part: %s seconds ---" % (time.time() - start_time))
            output = samples[0]
            outputs.append(output.permute(1,0,2))
            z = z.unsqueeze(3)

        self.prev_outputs = outputs
        return outputs

    def training_step(self, batch, batch_idx, reduce_loss=True):
        self.set_inputs(batch)
        latents = self.get_latents(self.inputs)
        loss = 0
        for i, mod in enumerate(self.output_mods):
            diffu = self.output_mod_diffus[i]
            x = self.targets[i].permute(1,0,2).unsqueeze(1) #time, batch, features -> batch, time, features
            t = torch.randint(0,self.num_diff_steps,(x.size(0),)).to(x.device)
            # losses = self.gd.training_losses(diffu, x, t, model_kwargs={'cond':latents[i][:,0,:]})
            losses = self.gd.training_losses(diffu, x, t, model_kwargs={'cond':latents[i]})
            loss += torch.mean(losses["loss"])

        self.log('loss', loss)
        return loss

    def test_step(self, batch, batch_idx):
        return super().test_step(batch, batch_idx)

    def test_epoch_end(self, outputs):
        return super().test_epoch_end(outputs)

    #to help debug XLA stuff, like missing ops, or data loading/compiling bottlenecks
    # see https://youtu.be/iwtpwQRdb3Y?t=1056
    # def on_epoch_end(self):
    #    import torch_xla.core.xla_model as xm
    #    import torch_xla.debug.metrics as met
    #    xm.master_print(met.metrics_report())


    #def optimizer_step(self, epoch, batch_idx, optimizer, optimizer_idx,
    #                           optimizer_closure, on_tpu, using_native_amp, using_lbfgs):
    #    optimizer.zero_grad()

# MIN_LOGP=-1e8
# MAX_LOGP=1e8

class NormalizingFlow(Distribution):
    def __init__(self, model, cond, temp=1.0, validate_args=False):
        self.model = model
        self.cond = cond
        self.temp = 1.0
        super(NormalizingFlow, self).__init__(torch.Size(), validate_args=validate_args)

    def log_prob(self,value):
        # print(value.shape)
        z, sldj, _ = self.model(x=value, cond=self.cond) #value comes in batch, time, features
        # print(z)
        # print(sldj)
        #logP = self.model.loss_generative(z, sldj, reduce=True)
        logP = self.model.loss_generative(z, sldj, reduce=False)
        # logP = torch.clamp(logP, MIN_LOGP, MAX_LOGP)
        # print(logP)
        # return logP.unsqueeze(0)
        return logP

    def sample(self):
        output, sldj, z = self.model(x=None, cond=self.cond, reverse=True, eps_std=self.temp, noise=None)
        return output

    def entropy(self):
        return torch.tensor([0]).float()

    def __repr__(self):
        return self.__class__.__name__
