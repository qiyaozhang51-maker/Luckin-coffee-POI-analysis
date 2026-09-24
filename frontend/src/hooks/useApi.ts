/**
 * @fileoverview 通用 API 请求 Hooks
 *
 * 职责：
 * - 封装 axios 的 GET/POST 请求逻辑，提供统一的 loading / error / data 状态管理
 * - 支持自动取消上一次未完成的请求（AbortController），防止竞态条件
 * - useApi 在 url 或 params 变化时自动重新请求，避免在每个页面重复编写 useEffect + try/catch
 * - useMutation 提供手动触发的 POST 请求能力，适合表单提交、评分等写操作
 *
 * @remarks
 * 竞态处理：当 url/params 快速变化时（如用户快速切换城市），上一个请求会被 abort，
 * 只有最新请求的结果会反映到 state 中，避免过期数据覆盖新数据。
 */

import { useState, useEffect, useCallback, useRef } from 'react';
import axios, { AxiosRequestConfig } from 'axios';

/**
 * API 请求状态接口
 * @template T - 响应数据类型
 */
interface UseApiState<T> {
  /** 请求返回的数据，请求中或未发起时为 null */
  data: T | null;
  /** 是否正在请求中 */
  loading: boolean;
  /** 错误信息，请求成功时为 null */
  error: string | null;
}

/**
 * 通用 GET 请求 Hook
 *
 * 自动管理 loading / error / data 三元状态。依赖（url、params、enabled）变化时
 * 自动发起新请求并取消上一次未完成的请求，内置 AbortController 防止竞态条件。
 *
 * @template T - 响应体的数据类型
 * @param url - 请求 URL，传 null 时不发起请求（用于条件性请求）
 * @param options.params - URL 查询参数，变化时自动重新请求
 * @param options.enabled - 是否启用请求，默认 true，设为 false 可暂缓请求
 * @returns data - 响应数据
 * @returns loading - 加载状态
 * @returns error - 错误信息
 * @returns refetch - 手动触发重新请求的函数
 *
 * @example
 * const { data, loading, error, refetch } = useApi<Store[]>('/api/stores', { params: { city: '上海' } });
 */
export function useApi<T = any>(
  url: string | null,
  options?: { params?: Record<string, any>; enabled?: boolean }
): UseApiState<T> & { refetch: () => void } {
  const [state, setState] = useState<UseApiState<T>>({ data: null, loading: false, error: null });
  /** 持有当前请求的 AbortController，用于在发起新请求前取消旧请求 */
  const abortRef = useRef<AbortController | null>(null);
  const enabled = options?.enabled ?? true;

  /**
   * 执行数据获取
   * useCallback 缓存的异步函数，依赖 url、params 序列化字符串、enabled。
   * 每次调用时先 abort 上一次未完成的请求，再发起新请求。
   */
  const fetchData = useCallback(async () => {
    if (!url || !enabled) return;

    // 取消上一次未完成的请求，避免过期数据覆盖
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setState((prev) => ({ ...prev, loading: true, error: null }));
    try {
      const config: AxiosRequestConfig = { signal: controller.signal };
      if (options?.params) config.params = options.params;
      const res = await axios.get<T>(url, config);
      // 仅在新请求未被取消时更新状态
      if (!controller.signal.aborted) {
        setState({ data: res.data, loading: false, error: null });
      }
    } catch (err: any) {
      // 忽略取消导致的错误（用户主动取消或新请求中断旧请求）
      if (err?.code === 'ERR_CANCELED' || err?.name === 'CanceledError') return;
      setState((prev) => ({ ...prev, loading: false, error: err?.message || '请求失败' }));
    }
  }, [url, JSON.stringify(options?.params), enabled]);

  /**
   * 副作用：fetchData 依赖变化时自动重新请求
   * 清理函数：组件卸载时取消未完成的请求，防止内存泄漏
   */
  useEffect(() => {
    fetchData();
    return () => abortRef.current?.abort();
  }, [fetchData]);

  return { ...state, refetch: fetchData };
}

/**
 * 通用 POST / Mutation Hook
 *
 * 封装 axios POST 请求，用于写操作（创建/更新/删除）。与 useApi 不同，
 * 该 hook 不自动发起请求，而是返回一个 mutate 函数由调用方手动触发。
 *
 * @template TReq - 请求体数据类型
 * @template TRes - 响应体数据类型
 * @param url - POST 请求的目标 URL
 * @param config - 额外的 axios 请求配置（如 headers、timeout 等）
 * @returns mutate - 发起 POST 请求的异步函数
 * @returns loading - 加载状态
 * @returns error - 错误信息
 * @returns data - 响应数据
 * @returns reset - 重置所有状态到初始值
 */
export function useMutation<TReq = any, TRes = any>(
  url: string,
  config?: AxiosRequestConfig
): {
  mutate: (data: TReq) => Promise<TRes>;
  loading: boolean;
  error: string | null;
  data: TRes | null;
  reset: () => void;
} {
  const [state, setState] = useState<{ loading: boolean; error: string | null; data: TRes | null }>({
    loading: false, error: null, data: null,
  });

  /**
   * 执行 POST 请求
   * useCallback 缓存：依赖 url，确保引用稳定
   */
  const mutate = useCallback(async (reqData: TReq): Promise<TRes> => {
    setState({ loading: true, error: null, data: null });
    try {
      const res = await axios.post<TRes>(url, reqData, config);
      setState({ loading: false, error: null, data: res.data });
      return res.data;
    } catch (err: any) {
      // 优先使用后端返回的 detail 字段作为错误信息
      const msg = err.response?.data?.detail || err.message || '请求失败';
      setState({ loading: false, error: msg, data: null });
      throw err;
    }
  }, [url]);

  /** 重置所有状态，用于清理上一次的请求结果/错误 */
  const reset = useCallback(() => setState({ loading: false, error: null, data: null }), []);

  return { ...state, mutate, reset };
}
