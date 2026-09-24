/**
 * @fileoverview 选址预测 Hook
 *
 * 职责：
 * - 封装选址预测 API 的调用（POST /api/prediction/score）
 * - 管理预测结果、加载状态和错误信息
 * - 提供 predict（执行预测）和 reset（清除结果）两个操作
 *
 * 使用场景：PredictionPage 中用户点击地图或输入经纬度后的选址评分。
 */

import { useCallback, useState } from 'react';
import axios from 'axios';

/**
 * 选址预测请求参数
 */
interface PredictionRequest {
  /** 经度 */
  lng: number;
  /** 纬度 */
  lat: number;
  /** 所在城市（用于城市层级特征） */
  city?: string;
}

/**
 * 特征贡献度
 * 描述某个特征对预测结果的正面或负面影响
 */
interface FeatureContribution {
  /** 特征名称（如"办公POI密度"、"交通可达性"） */
  feature: string;
  /** 特征标准化后的值 */
  value: number;
  /** 特征重要性得分（对预测结果的贡献程度） */
  importance: number;
}

/**
 * 选址预测结果
 */
interface PredictionResult {
  /** 适宜度评分 (0-1)，越高越适合选址 */
  score: number;
  /** 评分位置的经度 */
  lng: number;
  /** 评分位置的纬度 */
  lat: number;
  /** 所在城市 */
  city: string;
  /** 所有输入特征的数值，key 为特征名 */
  features: Record<string, number>;
  /** 正面贡献 Top-N 特征（提升评分的主要因素） */
  top_positive: FeatureContribution[];
  /** 负面贡献 Top-N 特征（拉低评分的主要因素） */
  top_negative: FeatureContribution[];
  /** 是否使用 XGBoost 模型评分（false 时为简化评分规则） */
  model_available: boolean;
}

/**
 * 选址预测 Hook
 *
 * 提供选址评分功能：发送定位坐标到后端模型，获取适宜度评分和特征分析。
 *
 * @returns predict - 执行预测的异步函数，接收 PredictionRequest 返回 PredictionResult
 * @returns result - 最新的预测结果，null 表示尚未评分或已重置
 * @returns loading - 是否正在评分中
 * @returns error - 评分错误信息
 * @returns reset - 清除当前结果和错误状态
 */
export function usePrediction() {
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<PredictionResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  /**
   * 执行选址预测
   * useCallback 缓存：无外部依赖，引用永不变
   * 自动管理 loading 状态，失败时设置 error 并抛出异常供上层处理
   */
  const predict = useCallback(async (req: PredictionRequest) => {
    setLoading(true);
    setError(null);
    try {
      const res = await axios.post<PredictionResult>('/api/prediction/score', req);
      setResult(res.data);
      return res.data;
    } catch (err: any) {
      const msg = err.response?.data?.detail || err.message || '评分失败';
      setError(msg);
      throw err;
    } finally {
      setLoading(false);
    }
  }, []);

  /** 重置预测状态，清除上一次的结果和错误信息 */
  const reset = useCallback(() => {
    setResult(null);
    setError(null);
  }, []);

  return { predict, result, loading, error, reset };
}
