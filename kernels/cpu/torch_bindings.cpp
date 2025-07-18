#include "cache.h"
#include "ops.h"
#include "core/registration.h"

#include <torch/library.h>

std::string init_cpu_threads_env(const std::string& cpu_ids);

void int8_scaled_mm(torch::Tensor& c, const torch::Tensor& a,
                    const torch::Tensor& b, const torch::Tensor& a_scales,
                    const torch::Tensor& b_scales,
                    const std::optional<torch::Tensor>& bias);

void int8_scaled_mm_azp(torch::Tensor& c, const torch::Tensor& a,
                        const torch::Tensor& b, const torch::Tensor& a_scales,
                        const torch::Tensor& b_scales,
                        const torch::Tensor& azp_adj,
                        const std::optional<torch::Tensor>& azp,
                        const std::optional<torch::Tensor>& bias);

void mla_decode_kvcache(torch::Tensor& out, torch::Tensor& query,
                        torch::Tensor& kv_cache, double scale,
                        torch::Tensor& block_tables, torch::Tensor& seq_lens);

int64_t init_shm_manager(const std::string& name, const int64_t group_size,
                         const int64_t rank);

std::string join_shm_manager(int64_t handle, const std::string& name);

void shm_allreduce(int64_t handle, torch::Tensor& data);

void shm_gather(int64_t handle, torch::Tensor& data,
                const std::optional<std::vector<torch::Tensor>>& outputs,
                int64_t dst);

void shm_all_gather(int64_t handle, const torch::Tensor& data,
                    torch::Tensor& output);

void shm_send_tensor_list(int64_t handle,
                          const std::vector<torch::Tensor>& tensor_list,
                          int64_t dst);

std::vector<torch::Tensor> shm_recv_tensor_list(int64_t handle, int64_t src);

TORCH_LIBRARY_EXPAND(TORCH_EXTENSION_NAME, ops) {
  // Aphrodite custom ops

  // Attention ops
  // Compute the attention between an input query and the cached keys/values
  // using PagedAttention.
  ops.def(
      "paged_attention_v1("
      "    Tensor! out, Tensor query, Tensor key_cache,"
      "    Tensor value_cache, int num_kv_heads, float scale,"
      "    Tensor block_tables, Tensor seq_lens, int block_size,"
      "    int max_seq_len, Tensor? alibi_slopes,"
      "    str kv_cache_dtype, Tensor k_scale, Tensor v_scale,"
      "    int tp_rank, int blocksparse_local_blocks,"
      "    int blocksparse_vert_stride, int blocksparse_block_size,"
      "    int blocksparse_head_sliding_step) -> ()");
  ops.impl("paged_attention_v1", torch::kCPU, &paged_attention_v1);

  // PagedAttention V2.
  ops.def(
      "paged_attention_v2("
      "    Tensor! out, Tensor! exp_sums, Tensor! max_logits,"
      "    Tensor! tmp_out, Tensor query, Tensor key_cache,"
      "    Tensor value_cache, int num_kv_heads, float scale,"
      "    Tensor block_tables, Tensor seq_lens, int block_size,"
      "    int max_seq_len, Tensor? alibi_slopes,"
      "    str kv_cache_dtype, Tensor k_scale, Tensor v_scale,"
      "    int tp_rank, int blocksparse_local_blocks,"
      "    int blocksparse_vert_stride, int blocksparse_block_size,"
      "    int blocksparse_head_sliding_step) -> ()");
  ops.impl("paged_attention_v2", torch::kCPU, &paged_attention_v2);

  // Activation ops

  // Activation function used in SwiGLU.
  ops.def("silu_and_mul(Tensor! out, Tensor input) -> ()");
  ops.impl("silu_and_mul", torch::kCPU, &silu_and_mul);

  // Activation function used in GeGLU with `none` approximation.
  ops.def("gelu_and_mul(Tensor! out, Tensor input) -> ()");
  ops.impl("gelu_and_mul", torch::kCPU, &gelu_and_mul);

  // Activation function used in GeGLU with `tanh` approximation.
  ops.def("gelu_tanh_and_mul(Tensor! out, Tensor input) -> ()");
  ops.impl("gelu_tanh_and_mul", torch::kCPU, &gelu_tanh_and_mul);

  // GELU implementation used in GPT-2.
  ops.def("gelu_new(Tensor! out, Tensor input) -> ()");
  ops.impl("gelu_new", torch::kCPU, &gelu_new);

  // Approximate GELU implementation.
  ops.def("gelu_fast(Tensor! out, Tensor input) -> ()");
  ops.impl("gelu_fast", torch::kCPU, &gelu_fast);

  // Quick GELU implementation.
  ops.def("gelu_quick(Tensor! out, Tensor input) -> ()");
  ops.impl("gelu_quick", torch::kCPU, &gelu_quick);

  // Layernorm
  // Apply Root Mean Square (RMS) Normalization to the input tensor.
  ops.def(
      "rms_norm(Tensor! out, Tensor input, Tensor weight, float epsilon) -> "
      "()");
  ops.impl("rms_norm", torch::kCPU, &rms_norm);

  // In-place fused Add and RMS Normalization.
  ops.def(
      "fused_add_rms_norm(Tensor! input, Tensor! residual, Tensor weight, "
      "float epsilon) -> ()");
  ops.impl("fused_add_rms_norm", torch::kCPU, &fused_add_rms_norm);

  // Rotary embedding
  // Apply GPT-NeoX or GPT-J style rotary embedding to query and key.
  ops.def(
      "rotary_embedding(Tensor positions, Tensor! query,"
      "                 Tensor! key, int head_size,"
      "                 Tensor cos_sin_cache, bool is_neox) -> ()");
  ops.impl("rotary_embedding", torch::kCPU, &rotary_embedding);

  // Quantization
#ifdef __AVX512F__
  // Compute int8 quantized tensor for given scaling factor.
  ops.def(
      "static_scaled_int8_quant(Tensor! out, Tensor input, Tensor scale,"
      "Tensor? azp) -> ()");
  ops.impl("static_scaled_int8_quant", torch::kCPU, &static_scaled_int8_quant);

  // Compute int8 quantized tensor and scaling factor
  ops.def(
      "dynamic_scaled_int8_quant(Tensor! out, Tensor input, Tensor! scale, "
      "Tensor!? azp) -> ()");
  ops.impl("dynamic_scaled_int8_quant", torch::kCPU,
           &dynamic_scaled_int8_quant);
  // W8A8 GEMM, supporting symmetric per-tensor or per-row/column
  // quantization.
  ops.def(
      "cutlass_scaled_mm(Tensor! out, Tensor a,"
      "                  Tensor b, Tensor a_scales,"
      "                  Tensor b_scales, Tensor? bias) -> ()");
  ops.impl("cutlass_scaled_mm", torch::kCPU, &int8_scaled_mm);
  // w8a8 GEMM, supporting asymmetric per-tensor or per-row/column
  // quantization.
  ops.def(
      "cutlass_scaled_mm_azp(Tensor! out, Tensor a,"
      "                  Tensor b, Tensor a_scales,"
      "                  Tensor b_scales, Tensor azp_adj,"
      "                  Tensor? azp, Tensor? bias) -> ()");
  ops.impl("cutlass_scaled_mm_azp", torch::kCPU, &int8_scaled_mm_azp);
#endif

// SHM CCL
#ifdef __AVX512F__
  ops.def("init_shm_manager(str name, int group_size, int rank) -> int",
          &init_shm_manager);
  ops.def("join_shm_manager(int handle, str name) -> str", &join_shm_manager);
  ops.def("shm_allreduce(int handle, Tensor! data) -> ()");
  ops.impl("shm_allreduce", torch::kCPU, &shm_allreduce);
  ops.def(
      "shm_gather(int handle, Tensor data, Tensor[](a!)? outputs, int dst) -> "
      "()");
  ops.impl("shm_gather", torch::kCPU, &shm_gather);
  ops.def(
      "shm_all_gather(int handle, Tensor data, Tensor! output) -> "
      "()");
  ops.impl("shm_all_gather", torch::kCPU, &shm_all_gather);
  ops.def(
      "shm_send_tensor_list(int handle, Tensor[](a) tensor_list, int dst) -> "
      "()");
  ops.impl("shm_send_tensor_list", torch::kCPU, &shm_send_tensor_list);
  ops.def("shm_recv_tensor_list(int handle, int src) -> Tensor[](a)",
          &shm_recv_tensor_list);
#endif
}

TORCH_LIBRARY_EXPAND(CONCAT(TORCH_EXTENSION_NAME, _cache_ops), cache_ops) {
  // Cache ops
  // Swap in (out) the cache blocks from src to dst.
  cache_ops.def(
      "swap_blocks(Tensor src, Tensor! dst, Tensor block_mapping) -> ()");
  cache_ops.impl("swap_blocks", torch::kCPU, &swap_blocks);

  // Copy the cache blocks from src to dst.
  cache_ops.def(
      "copy_blocks(Tensor(a!)[] key_caches, Tensor[](b!) value_caches, "
      "Tensor block_mapping) -> ()");
  cache_ops.impl("copy_blocks", torch::kCPU, &copy_blocks);

  // Reshape the key and value tensors and cache them.
  cache_ops.def(
      "reshape_and_cache(Tensor key, Tensor value,"
      "                  Tensor! key_cache, Tensor! value_cache,"
      "                  Tensor slot_mapping,"
      "                  str kv_cache_dtype,"
      "                  Tensor k_scale, Tensor v_scale) -> ()");
  cache_ops.impl("reshape_and_cache", torch::kCPU, &reshape_and_cache);

  cache_ops.def(
      "concat_and_cache_mla(Tensor kv_c, Tensor k_pe,"
      "                     Tensor! kv_cache,"
      "                     Tensor slot_mapping,"
      "                     str kv_cache_dtype,"
      "                     Tensor scale) -> ()");
  cache_ops.impl("concat_and_cache_mla", torch::kCPU, &concat_and_cache_mla);
}

TORCH_LIBRARY_EXPAND(CONCAT(TORCH_EXTENSION_NAME, _utils), utils) {
  // CPU utils
  utils.def("init_cpu_threads_env(str cpu_ids) -> str", &init_cpu_threads_env);
}

TORCH_LIBRARY_EXPAND(CONCAT(TORCH_EXTENSION_NAME, _cpu), cpu_ops) {
  cpu_ops.def(
      "mla_decode_kvcache("
      "   Tensor! out, Tensor query, Tensor kv_cache,"
      "   float scale, Tensor block_tables, Tensor seq_lens) -> ()");
  cpu_ops.impl("mla_decode_kvcache", torch::kCPU, &mla_decode_kvcache);
}

REGISTER_EXTENSION(TORCH_EXTENSION_NAME)
