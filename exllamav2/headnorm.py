from __future__ import annotations
import torch
from torch import nn
from exllamav2.module import ExLlamaV2Module
from exllamav2.ext import exllamav2_ext as ext_c, none_tensor

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from exllamav2.model import ExLlamaV2

class ExLlamaV2HeadNorm(ExLlamaV2Module):

    name: str = "LayerNorm"

    layernorm: nn.LayerNorm | None
    weight: nn.Parameter | None
    bias: nn.Parameter | None
    variance_epsilon: float

    head_dim: int
    num_heads: int


    def __init__(
        self,
        model: ExLlamaV2,
        key: str,
        num_heads: int,
        head_dim: int,
        archparams = None
    ):
        super().__init__(model, key, archparams)

        self.layernorm = None
        self.weight = None
        self.bias = None
        self.variance_epsilon = self.model.config.norm_eps

        self.head_dim = head_dim
        self.num_heads = num_heads


    @torch.inference_mode
    def load(self):

        w = self.load_weight()

        if isinstance(w, tuple):
            weight = w[0]
            bias = w[1]
            if len(weight.shape) == 1 and weight.shape[0] == self.model.config.head_dim:
                weight = nn.Parameter(weight.repeat(self.num_heads, 1))
                bias = nn.Parameter(bias.repeat(self.num_heads, 1))
        else:
            weight = w
            bias = None
            if len(weight.shape) == 1 and weight.shape[0] == self.model.config.head_dim:
                weight = nn.Parameter(weight.repeat(self.num_heads, 1))

        assert isinstance(weight, nn.Parameter)
        assert bias is None or isinstance(bias, nn.Parameter)

        self.layernorm = nn.LayerNorm(
            self.model.config.hidden_size,
            elementwise_affine = True,
            bias = bias is not None
        )

        self.layernorm.weight = weight
        self.weight = weight

        if bias is not None:
            self.layernorm.bias = bias
            self.bias = bias

        assert self.weight.shape == (self.num_heads, self.head_dim), "Head norm tensor shape mismatch"

        if self.archparams.norm_constant_bias != 0:
            self.weight += self.archparams.norm_constant_bias


    def unload(self):

        self.layernorm = None
        self.weight = None
        self.bias = None


    def numel(self):

        return 0
        # return self.layernorm.weight.data.numel()


    def get_weight(self) -> torch.Tensor | tuple[torch.Tensor, torch.Tensor]:

        w = self.weight.data
        if self.archparams.norm_constant_bias != 0:
            return w - self.archparams.norm_constant_bias

        if self.bias is not None: return w, self.bias
        return w


    def weight_footprint(self) -> int:

        hidden_size = self.model.config.hidden_size
        return hidden_size * 2


    def scratch_space_fixed(self) -> int:

        return 0


    def scratch_space(self) -> int:

        return 0


    def forward(
        self,
        hidden_states: torch.Tensor,
        cache = None,
        attn_params = None,
        past_len = None,
        intermediates: bool = False,
        loras = None,
        **kwargs
    ) -> torch.Tensor | dict[str: torch.Tensor]:

        ext_c.head_norm(hidden_states,
                        self.weight.data,
                        self.bias.data if self.bias is not None else none_tensor,
                        hidden_states,
                        self.variance_epsilon,
                        self.archparams.headnorm == "rmsnorm")

        if intermediates:
            return {"hidden_states": hidden_states}
        else:
            return hidden_states


    def forward_torch(
        self,
        hidden_states: torch.Tensor,
        cache = None,
        attn_params = None,
        past_len = None,
        intermediates: bool = False,
        loras = None,
        **kwargs
    ) -> torch.Tensor | dict[str: torch.Tensor]:

        if self.archparams.headnorm == "rmsnorm":
            input_dtype = hidden_states.dtype
            hidden_states = hidden_states.to(torch.float32)
            variance = hidden_states.pow(2).mean(-1, keepdim = True)
            hidden_states = hidden_states * torch.rsqrt(variance + self.variance_epsilon)
            hidden_states = self.weight.to(torch.float32) * hidden_states
            hidden_states = hidden_states.to(input_dtype)
        else:
            input_dtype = hidden_states.dtype
            hidden_states = hidden_states.to(torch.float32)
            mean = hidden_states.mean(-1, keepdim = True)
            variance = (hidden_states - mean).pow(2).mean(-1, keepdim = True)
            hidden_states = (hidden_states - mean) * torch.rsqrt(variance + self.variance_epsilon)
            hidden_states = self.weight.to(torch.float32) * hidden_states
            hidden_states = hidden_states.to(input_dtype)

        if intermediates:
            return {"hidden_states": hidden_states}
        else:
            return hidden_states


