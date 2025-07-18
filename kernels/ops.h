#pragma once

#include <optional>
#include <torch/library.h>

#include "core/scalar_type.hpp"

#include <vector>

inline torch::Tensor weak_ref_tensor(torch::Tensor& tensor) {
  // Ensure tensor is on CUDA
  if (!tensor.is_cuda()) {
    throw std::runtime_error("Tensor must be on CUDA device");
  }

  // Get the raw data pointer
  void* data_ptr = tensor.data_ptr();

  // Get tensor sizes and strides
  std::vector<int64_t> sizes = tensor.sizes().vec();
  std::vector<int64_t> strides = tensor.strides().vec();

  // Get tensor options (dtype, device)
  auto options = tensor.options();

  // Create a new tensor from the raw data pointer
  auto new_tensor = torch::from_blob(data_ptr, sizes, strides, options);

  return new_tensor;
}

void paged_attention_v1(
    torch::Tensor& out, torch::Tensor& query, torch::Tensor& key_cache,
    torch::Tensor& value_cache, int64_t num_kv_heads, double scale,
    torch::Tensor& block_tables, torch::Tensor& seq_lens, int64_t block_size,
    int64_t max_seq_len, const std::optional<torch::Tensor>& alibi_slopes,
    const std::string& kv_cache_dtype, torch::Tensor& k_scale,
    torch::Tensor& v_scale, const int64_t tp_rank,
    const int64_t blocksparse_local_blocks,
    const int64_t blocksparse_vert_stride, const int64_t blocksparse_block_size,
    const int64_t blocksparse_head_sliding_step);

void paged_attention_v2(
    torch::Tensor& out, torch::Tensor& exp_sums, torch::Tensor& max_logits,
    torch::Tensor& tmp_out, torch::Tensor& query, torch::Tensor& key_cache,
    torch::Tensor& value_cache, int64_t num_kv_heads, double scale,
    torch::Tensor& block_tables, torch::Tensor& seq_lens, int64_t block_size,
    int64_t max_seq_len, const std::optional<torch::Tensor>& alibi_slopes,
    const std::string& kv_cache_dtype, torch::Tensor& k_scale,
    torch::Tensor& v_scale, const int64_t tp_rank,
    const int64_t blocksparse_local_blocks,
    const int64_t blocksparse_vert_stride, const int64_t blocksparse_block_size,
    const int64_t blocksparse_head_sliding_step);

#ifndef USE_ROCM
void merge_attn_states(torch::Tensor& output,
                       std::optional<torch::Tensor> output_lse,
                       const torch::Tensor& prefix_output,
                       const torch::Tensor& prefix_lse,
                       const torch::Tensor& suffix_output,
                       const torch::Tensor& suffix_lse);
#endif

void rms_norm(torch::Tensor& out, torch::Tensor& input, torch::Tensor& weight,
              double epsilon);

void fused_add_rms_norm(torch::Tensor& input, torch::Tensor& residual,
                        torch::Tensor& weight, double epsilon);

void rms_norm_static_fp8_quant(torch::Tensor& out, torch::Tensor& input,
                               torch::Tensor& weight, torch::Tensor& scale,
                               double epsilon);

void fused_add_rms_norm_static_fp8_quant(torch::Tensor& out,
                                         torch::Tensor& input,
                                         torch::Tensor& residual,
                                         torch::Tensor& weight,
                                         torch::Tensor& scale, double epsilon);

void rms_norm_dynamic_per_token_quant(torch::Tensor& out,
                                      torch::Tensor const& input,
                                      torch::Tensor const& weight,
                                      torch::Tensor& scales,
                                      double const epsilon,
                                      std::optional<torch::Tensor> scale_ub,
                                      std::optional<torch::Tensor> residual);

void rotary_embedding(torch::Tensor& positions, torch::Tensor& query,
                      torch::Tensor& key, int64_t head_size,
                      torch::Tensor& cos_sin_cache, bool is_neox);

void batched_rotary_embedding(torch::Tensor& positions, torch::Tensor& query,
                              torch::Tensor& key, int64_t head_size,
                              torch::Tensor& cos_sin_cache, bool is_neox,
                              int64_t rot_dim,
                              torch::Tensor& cos_sin_cache_offsets);

void silu_and_mul(torch::Tensor& out, torch::Tensor& input);

void silu_and_mul_quant(torch::Tensor& out, torch::Tensor& input,
                        torch::Tensor& scale);

void mul_and_silu(torch::Tensor& out, torch::Tensor& input);

void gelu_and_mul(torch::Tensor& out, torch::Tensor& input);

void gelu_tanh_and_mul(torch::Tensor& out, torch::Tensor& input);

void fatrelu_and_mul(torch::Tensor& out, torch::Tensor& input,
                     double threshold);

void gelu_new(torch::Tensor& out, torch::Tensor& input);

void gelu_fast(torch::Tensor& out, torch::Tensor& input);

void gelu_quick(torch::Tensor& out, torch::Tensor& input);

void advance_step_flashattn(int64_t num_seqs, int64_t num_queries,
                            int64_t block_size, torch::Tensor& input_tokens,
                            torch::Tensor& sampled_token_ids,
                            torch::Tensor& input_positions,
                            torch::Tensor& seq_lens,
                            torch::Tensor& slot_mapping,
                            torch::Tensor& block_tables);

void advance_step_flashinfer(
    int64_t num_seqs, int64_t num_queries, int64_t block_size,
    torch::Tensor& input_tokens, torch::Tensor& sampled_token_ids,
    torch::Tensor& input_positions, torch::Tensor& seq_lens,
    torch::Tensor& slot_mapping, torch::Tensor& block_tables,
    torch::Tensor& paged_kv_indices, torch::Tensor& paged_kv_indptr,
    torch::Tensor& paged_kv_last_page_len, torch::Tensor& block_table_bounds);

void cutlass_mla_decode(torch::Tensor const& out, torch::Tensor const& q_nope,
                        torch::Tensor const& q_pe,
                        torch::Tensor const& kv_c_and_k_pe_cache,
                        torch::Tensor const& seq_lens,
                        torch::Tensor const& page_table, double scale);

torch::Tensor get_cuda_view_from_cpu_tensor(torch::Tensor& cpu_tensor);

using fptr_t = int64_t;
fptr_t init_custom_ar(const std::vector<int64_t>& fake_ipc_ptrs,
                      torch::Tensor& rank_data, int64_t rank,
                      bool fully_connected);
void all_reduce(fptr_t _fa, torch::Tensor& inp, torch::Tensor& out,
                fptr_t reg_buffer, int64_t reg_buffer_sz_bytes);
void dispose(fptr_t _fa);
int64_t meta_size();
void register_buffer(fptr_t _fa, const std::vector<int64_t>& fake_ipc_ptrs);
std::tuple<std::vector<int64_t>, std::vector<int64_t>>
get_graph_buffer_ipc_meta(fptr_t _fa);
void register_graph_buffers(fptr_t _fa,
                            const std::vector<std::vector<int64_t>>& handles,
                            const std::vector<std::vector<int64_t>>& offsets);
std::tuple<int64_t, torch::Tensor> allocate_shared_buffer_and_handle(
    int64_t size);
int64_t open_mem_handle(torch::Tensor& mem_handle);
void free_shared_buffer(int64_t buffer);

void selective_scan_fwd(const torch::Tensor& u, const torch::Tensor& delta,
                        const torch::Tensor& A, const torch::Tensor& B,
                        const torch::Tensor& C,
                        const std::optional<torch::Tensor>& D_,
                        const std::optional<torch::Tensor>& z_,
                        const std::optional<torch::Tensor>& delta_bias_,
                        bool delta_softplus,
                        const std::optional<torch::Tensor>& query_start_loc,
                        const std::optional<torch::Tensor>& cache_indices,
                        const std::optional<torch::Tensor>& has_initial_state,
                        const torch::Tensor& ssm_states, int64_t pad_slot_id);

void causal_conv1d_update(const at::Tensor& x, const at::Tensor& conv_state,
                          const at::Tensor& weight,
                          const std::optional<at::Tensor>& bias_,
                          bool silu_activation,
                          const std::optional<at::Tensor>& cache_seqlens_,
                          const std::optional<at::Tensor>& conv_state_indices_,
                          int64_t pad_slot_id);

void causal_conv1d_fwd(const at::Tensor& x, const at::Tensor& weight,
                       const std::optional<at::Tensor>& bias_,
                       const std::optional<at::Tensor>& conv_states,
                       const std::optional<at::Tensor>& query_start_loc,
                       const std::optional<at::Tensor>& cache_indices,
                       const std::optional<at::Tensor>& has_initial_state,
                       bool silu_activation, int64_t pad_slot_id);

torch::Tensor permute_cols(torch::Tensor const& A, torch::Tensor const& perm);

// Sampling kernels
#ifndef USE_ROCM
torch::Tensor sampling_from_probs(torch::Tensor probs,
                                  torch::Tensor uniform_samples,
                                  bool deterministic);
std::vector<torch::Tensor> top_p_sampling_from_probs(
    torch::Tensor probs, torch::Tensor uniform_samples,
    std::optional<torch::Tensor> maybe_top_p_arr, double top_p_val,
    bool deterministic);
std::vector<torch::Tensor> top_k_sampling_from_probs(
    torch::Tensor probs, torch::Tensor uniform_samples,
    std::optional<torch::Tensor> maybe_top_k_arr, int64_t top_k_val,
    bool deterministic);
std::vector<torch::Tensor> min_p_sampling_from_probs(
    torch::Tensor probs, torch::Tensor uniform_samples,
    std::optional<torch::Tensor> maybe_min_p_arr, double min_p_val,
    bool deterministic);
std::vector<torch::Tensor> top_k_top_p_sampling_from_probs(
    torch::Tensor probs, torch::Tensor uniform_samples,
    std::optional<torch::Tensor> maybe_top_k_arr, double top_k_val,
    std::optional<torch::Tensor> maybe_top_p_arr, double top_p_val,
    bool deterministic);
torch::Tensor top_p_renorm_prob(torch::Tensor probs,
                                std::optional<torch::Tensor> maybe_top_p_arr,
                                double top_p_val);
torch::Tensor top_k_renorm_prob(torch::Tensor probs,
                                std::optional<torch::Tensor> maybe_top_k_arr,
                                int64_t top_k_val);
torch::Tensor top_k_mask_logits(torch::Tensor logits,
                                std::optional<torch::Tensor> maybe_top_k_arr,
                                int64_t top_k_val);

#endif

// Quantization kernels
#ifndef USE_ROCM
torch::Tensor aqlm_gemm(const torch::Tensor& input, const torch::Tensor& codes,
                        const torch::Tensor& codebooks,
                        const torch::Tensor& scales,
                        const std::vector<int64_t>& codebook_partition_sizes,
                        const std::optional<torch::Tensor>& bias);

torch::Tensor aqlm_dequant(
    const torch::Tensor& codes, const torch::Tensor& codebooks,
    const std::vector<int64_t>& codebook_partition_sizes);

torch::Tensor awq_gemm(torch::Tensor _in_feats, torch::Tensor _kernel,
                       torch::Tensor _scaling_factors, torch::Tensor _zeros,
                       int64_t split_k_iters);

torch::Tensor awq_dequantize(torch::Tensor _kernel,
                             torch::Tensor _scaling_factors,
                             torch::Tensor _zeros, int64_t split_k_iters,
                             int64_t thx, int64_t thy);

torch::Tensor permute_cols(torch::Tensor const& A, torch::Tensor const& perm);
#endif

torch::Tensor ggml_dequantize(torch::Tensor W, int64_t type, int64_t m,
                              int64_t n,
                              std::optional<at::ScalarType> const& dtype);

torch::Tensor ggml_mul_mat_vec_a8(torch::Tensor W, torch::Tensor X,
                                  int64_t type, int64_t row);

torch::Tensor ggml_mul_mat_a8(torch::Tensor W, torch::Tensor X, int64_t type,
                              int64_t row);

torch::Tensor ggml_moe_a8(torch::Tensor X, torch::Tensor W,
                          torch::Tensor sorted_token_ids,
                          torch::Tensor expert_ids,
                          torch::Tensor num_tokens_post_padded, int64_t type,
                          int64_t row, int64_t top_k, int64_t tokens);

int64_t ggml_moe_get_block_size(int64_t type);

#ifndef USE_ROCM

bool cutlass_scaled_mm_supports_fp4(int64_t cuda_device_capability);
bool cutlass_scaled_mm_supports_fp8(int64_t cuda_device_capability);
bool cutlass_scaled_mm_supports_block_fp8(int64_t cuda_device_capability);
bool cutlass_group_gemm_supported(int64_t cuda_device_capability);

void cutlass_scaled_fp4_mm(torch::Tensor& D, torch::Tensor const& A,
                           torch::Tensor const& B, torch::Tensor const& A_sf,
                           torch::Tensor const& B_sf,
                           torch::Tensor const& alpha);

void cutlass_scaled_mm(torch::Tensor& out, torch::Tensor const& a,
                       torch::Tensor const& b, torch::Tensor const& a_scales,
                       torch::Tensor const& b_scales,
                       std::optional<torch::Tensor> const& bias);

void cutlass_moe_mm(
    torch::Tensor& out_tensors, torch::Tensor const& a_tensors,
    torch::Tensor const& b_tensors, torch::Tensor const& a_scales,
    torch::Tensor const& b_scales, torch::Tensor const& expert_offsets,
    torch::Tensor const& problem_sizes, torch::Tensor const& a_strides,
    torch::Tensor const& b_strides, torch::Tensor const& c_strides);

void get_cutlass_moe_mm_data(
    const torch::Tensor& topk_ids, torch::Tensor& expert_offsets,
    torch::Tensor& problem_sizes1, torch::Tensor& problem_sizes2,
    torch::Tensor& input_permutation, torch::Tensor& output_permutation,
    const int64_t num_experts, const int64_t n, const int64_t k);

void cutlass_scaled_mm_azp(torch::Tensor& out, torch::Tensor const& a,
                           torch::Tensor const& b,
                           torch::Tensor const& a_scales,
                           torch::Tensor const& b_scales,
                           torch::Tensor const& azp_adj,
                           std::optional<torch::Tensor> const& azp,
                           std::optional<torch::Tensor> const& bias);

bool cutlass_sparse_scaled_mm_supported(int64_t cuda_device_capability);

void cutlass_scaled_sparse_mm(torch::Tensor& out, torch::Tensor const& a,
                              torch::Tensor const& b, torch::Tensor const& e,
                              torch::Tensor const& a_scales,
                              torch::Tensor const& b_scales,
                              std::optional<torch::Tensor> const& bias);

std::vector<torch::Tensor> cutlass_sparse_compress(torch::Tensor const& a);

void scaled_fp4_quant(torch::Tensor& output, torch::Tensor const& input,
                      torch::Tensor& output_scale,
                      torch::Tensor const& input_scale);
#endif

void static_scaled_int8_quant(torch::Tensor& out, torch::Tensor const& input,
                              torch::Tensor const& scale,
                              std::optional<torch::Tensor> const& azp);

void dynamic_scaled_int8_quant(torch::Tensor& out, torch::Tensor const& input,
                               torch::Tensor& scales,
                               std::optional<torch::Tensor> const& azp);

torch::Tensor gptq_gemm(torch::Tensor a, torch::Tensor b_q_weight,
                        torch::Tensor b_gptq_qzeros,
                        torch::Tensor b_gptq_scales, torch::Tensor b_g_idx,
                        bool use_exllama, int64_t bit);

void gptq_shuffle(torch::Tensor q_weight, torch::Tensor q_perm, int64_t bit);

void static_scaled_fp8_quant(torch::Tensor& out, torch::Tensor const& input,
                             torch::Tensor const& scale);

void dynamic_scaled_fp8_quant(torch::Tensor& out, torch::Tensor const& input,
                              torch::Tensor& scale);

void dynamic_per_token_scaled_fp8_quant(
    torch::Tensor& out, torch::Tensor const& input, torch::Tensor& scale,
    std::optional<torch::Tensor> const& scale_ub);

#ifndef USE_ROCM
torch::Tensor vptq_gemm(const torch::Tensor& input,
                        const torch::Tensor& q_indice,
                        const torch::Tensor& centroids,
                        const torch::Tensor& weight_scale,
                        const torch::Tensor& weight_bias,
                        const std::vector<int64_t>& g_i_o,
                        const c10::optional<torch::Tensor>& q_indice_residual,
                        const c10::optional<torch::Tensor>& residual_centroids,
                        const c10::optional<torch::Tensor>& q_indice_outliers,
                        const c10::optional<torch::Tensor>& outliers_centroids,
                        const c10::optional<torch::Tensor>& invperm,
                        const c10::optional<torch::Tensor>& bias);

torch::Tensor vptq_dequant(
    const torch::Tensor& q_indice, const torch::Tensor& centroids,
    const torch::Tensor& weight_scale, const torch::Tensor& weight_bias,
    const std::vector<int64_t>& g_i_o,
    const c10::optional<torch::Tensor>& q_indice_residual,
    const c10::optional<torch::Tensor>& residual_centroids,
    const c10::optional<torch::Tensor>& q_indice_outliers,
    const c10::optional<torch::Tensor>& outliers_centroids,
    const c10::optional<torch::Tensor>& invperm);
#endif

// SqueezeLLM
void squeezellm_gemm(torch::Tensor vec, torch::Tensor mat, torch::Tensor mul,
                     torch::Tensor lookup_table);
