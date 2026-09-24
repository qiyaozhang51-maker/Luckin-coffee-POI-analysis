/**
 * @fileoverview 城市列表 Hook
 *
 * 职责：
 * - 从后端获取所有门店覆盖的城市列表及统计信息
 * - 按品牌（瑞幸/星巴克）拆分城市数据，便于多维度分析
 * - 生成 Select 组件所需的 options 格式（城市名 + 门店数）
 * - 汇总全国门店总数、覆盖城市数等关键 KPI
 *
 * 多个页面（HomePage、EvolutionPage、PoiAnalysisPage、ComparisonPage）共用此 Hook。
 */

import { useMemo } from 'react';
import { useApi } from './useApi';

/**
 * 城市门店统计信息
 */
export interface CityInfo {
  /** 城市名称 */
  city: string;
  /** 品牌标识：'luckin' 或 'starbucks' */
  brand: string;
  /** 该城市该品牌的门店数量 */
  store_count: number;
  /** 该城市该品牌首家门店的开业日期 */
  first_open_date: string | null;
  /** 该城市该品牌最新门店的开业日期 */
  last_open_date: string | null;
  /** 城市层级标签（如"一线"、"新一线"、"二线"） */
  tier_label: string | null;
}

/**
 * 城市列表 Hook
 *
 * 获取所有城市的门店统计信息，按品牌拆分并汇总关键 KPI。
 * 所有派生数据通过 useMemo 缓存，仅在原始数据变化时重新计算。
 *
 * @returns cities - 完整城市列表（含所有品牌）
 * @returns luckinCities - 仅瑞幸品牌的城市列表
 * @returns starbucksCities - 仅星巴克品牌的城市列表
 * @returns cityOptions - 适配 Ant Design Select 组件的数据格式 { label, value }
 * @returns totalLuckin - 瑞幸全国门店总数
 * @returns totalStarbucks - 星巴克全国门店总数
 * @returns citiesCovered - 瑞幸覆盖城市数
 * @returns loading - 加载状态
 * @returns error - 错误信息
 * @returns refetch - 手动重新获取
 */
export function useCities() {
  const { data, loading, error, refetch } = useApi<CityInfo[]>('/api/stores/cities/list');

  /** 筛选瑞幸品牌城市列表 */
  const luckinCities = useMemo(() => (data || []).filter((c) => c.brand === 'luckin'), [data]);
  /** 筛选星巴克品牌城市列表 */
  const starbucksCities = useMemo(() => (data || []).filter((c) => c.brand === 'starbucks'), [data]);

  /** 生成 Select 组件选项：城市名 + 门店数，按瑞幸门店数排序 */
  const cityOptions = useMemo(
    () =>
      luckinCities.map((c) => ({
        label: `${c.city} (${c.store_count})`,
        value: c.city,
      })),
    [luckinCities],
  );

  /** 瑞幸全国门店总数 */
  const totalLuckin = useMemo(() => luckinCities.reduce((s, c) => s + c.store_count, 0), [luckinCities]);
  /** 星巴克全国门店总数 */
  const totalStarbucks = useMemo(() => starbucksCities.reduce((s, c) => s + c.store_count, 0), [starbucksCities]);
  /** 瑞幸覆盖城市数 */
  const citiesCovered = luckinCities.length;

  return {
    cities: data || [],
    luckinCities,
    starbucksCities,
    cityOptions,
    totalLuckin,
    totalStarbucks,
    citiesCovered,
    loading,
    error,
    refetch,
  };
}
