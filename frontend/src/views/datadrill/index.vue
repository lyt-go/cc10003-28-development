<template>
  <section class="page" data-module="datadrill">
    <header class="page-head">
      <div>
        <h2>多维下钻管理</h2>
        <p class="page-desc">定义下钻分析路径；启用时冻结每层维度名、每层度量、路径深度与默认展开层，看板只能引用已启用版本。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记下钻路径</button>
        <button class="btn" type="button" @click="exportRows">导出多维下钻清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ formatCell(row[column]) }}</td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
            <button class="link" type="button" @click="openConfig(row)">修订配置</button>
            <button class="link" type="button" @click="openReport(row)">版本报表</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无多维下钻数据，可先登记下钻路径</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条多维下钻记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <!-- 修订路径配置（启用后禁止修改，需关闭后再修订重新启用产生新版本） -->
    <Modal :open="configOpen" title="修订下钻路径配置" @close="configOpen = false">
      <div v-if="configTarget">
        <p class="page-desc">
          路径 {{ configTarget['路径编号'] }}（当前状态：{{ configTarget['status'] }}）。
          已启用路径需先关闭再修订；重新启用会冻结新版本，已发布看板保留旧快照。
        </p>
        <form class="filter-bar" style="flex-direction: column; align-items: stretch">
          <label v-for="field in configFields" :key="field" class="filter-item">
            <span>{{ field }}</span>
            <textarea
              v-if="field === '每层度量' || field === '每层维度名' || field === '下钻层级'"
              rows="3"
              :value="configForm[field]"
              :disabled="configTarget.status === '已启用'"
              @input="configForm[field] = ($event.target as HTMLTextAreaElement).value"
            />
            <input
              v-else
              :value="configForm[field]"
              :disabled="configTarget.status === '已启用'"
              @input="configForm[field] = ($event.target as HTMLInputElement).value"
            />
          </label>
          <p class="page-desc">
            填写口径：每层维度名用「region,channel,store」；每层度量按层用 | 分隔、层内用逗号，如「销售额,订单量|销售额,订单量|销售额,订单量」；
            路径深度 1~8；默认展开层 1~路径深度；每层度量项不超过 5 且各层一致。
          </p>
        </form>
        <p v-if="configHint" class="error-text">{{ configHint }}</p>
      </div>
      <template #footer>
        <button class="btn ghost" type="button" @click="configOpen = false">取消</button>
        <button
          class="btn primary"
          type="button"
          :disabled="configTarget?.status === '已启用'"
          @click="saveConfig"
        >保存修订</button>
      </template>
    </Modal>

    <!-- 版本报表：冻结版本、看板引用与审计快照 -->
    <Modal :open="reportOpen" title="下钻路径版本报表" width="820px" @close="reportOpen = false">
      <div v-if="report">
        <p class="section-title">路径</p>
        <dl class="kv-grid">
          <dt>路径编号</dt><dd>{{ report['路径']['路径编号'] }}</dd>
          <dt>状态 / 当前版本</dt><dd>{{ report['路径']['status'] }} · {{ report['路径']['当前版本号'] ?? '—' }}</dd>
          <dt>每层维度名</dt><dd>{{ formatCell(report['路径']['每层维度名']) }}</dd>
          <dt>默认展开层</dt><dd>{{ report['路径']['默认展开层'] ?? '—' }}</dd>
        </dl>

        <p class="section-title">冻结版本（{{ report['版本列表'].length }}）</p>
        <div v-for="version in report['版本列表']" :key="version.id" class="audit-item">
          <strong>{{ version['版本号'] }} · {{ version['status'] }}</strong>
          <span class="page-desc" style="margin-left: 8px">{{ version['启用时间'] }} ~ {{ version['停用时间'] ?? '至今' }}</span>
          <pre>{{ JSON.stringify(version['快照'], null, 2) }}</pre>
        </div>
        <p v-if="!report['版本列表'].length" class="empty-state">尚未启用，暂无冻结版本</p>

        <p class="section-title">看板引用（{{ report['看板引用'].length }}）</p>
        <div v-for="ref in report['看板引用']" :key="ref.id" class="audit-item">
          <strong>{{ ref['看板编号'] }}</strong> 引用 {{ ref['版本号'] }}
          <span class="badge" :class="ref['引用状态'] === '生效' ? 'ok' : 'bad'" style="margin-left: 6px">{{ ref['引用状态'] }}</span>
          <div v-if="ref['失效原因']" class="page-desc">{{ ref['失效原因'] }}；新可用版本：{{ ref['新可用版本'] ?? '无' }}</div>
        </div>
        <p v-if="!report['看板引用'].length" class="empty-state">暂无看板引用</p>

        <p class="section-title">审计记录</p>
        <div v-for="log in report['审计记录']" :key="log.id" class="audit-item">
          <strong>{{ log['动作'] }} · {{ log['结果'] }}</strong>
          <span class="page-desc" style="margin-left: 8px">{{ log['时间'] }} · {{ log['说明'] }}</span>
          <pre>{{ JSON.stringify(log['快照'], null, 2) }}</pre>
        </div>
      </div>
    </Modal>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import Modal from '@/components/Modal.vue'
import { request } from '@/api/client'

type Row = Record<string, any>

const ENDPOINT = '/api/datadrill'
const columns = ['路径编号', '起始维度', '每层维度名', '每层度量', '路径深度', '默认展开层', '当前版本号']
const actions = ['提交路径评审', '启用路径', '关闭路径']
const statuses = ['草稿', '路径评审中', '已启用', '已关闭']
const stats = [{ label: '下钻路径总数', value: 0 }, { label: '已启用路径', value: 0 }, { label: '路径评审中', value: 0 }]
const configFields = ['起始维度', '下钻层级', '每层维度名', '每层度量', '路径深度', '默认展开层']

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = ['路径编号', '起始维度', '下钻层级']

const configOpen = ref(false)
const configTarget = ref<Row | null>(null)
const configForm = ref<Record<string, string>>({})
const configHint = ref('')

const reportOpen = ref(false)
const report = ref<Row | null>(null)

function formatCell(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (Array.isArray(value)) {
    return value.map((item) => (Array.isArray(item) ? item.join('/') : String(item))).join(' | ')
  }
  return String(value)
}

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '下钻路径登记入口尚未接入审批流'
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    const payload = await response.json()
    if (!response.ok || payload.ok === false) {
      throw new Error(payload.message ?? '多维下钻动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '多维下钻操作失败'
  }
}

function openConfig(row: Row) {
  configTarget.value = row
  configHint.value = ''
  configForm.value = {}
  for (const field of configFields) {
    configForm.value[field] = formatCell(row[field]) === '—' ? '' : String(
      Array.isArray(row[field]) ? formatCell(row[field]) : row[field] ?? '',
    )
  }
  configOpen.value = true
}

async function saveConfig() {
  if (!configTarget.value) return
  configHint.value = ''
  try {
    const response = await request(`${ENDPOINT}/${configTarget.value.id}/config`, {
      method: 'PATCH',
      body: JSON.stringify({ values: configForm.value }),
    })
    const payload = await response.json()
    if (!response.ok || payload.ok === false) {
      throw new Error(payload.message ?? '配置修订失败')
    }
    configOpen.value = false
    await reload()
  } catch (error) {
    configHint.value = error instanceof Error ? error.message : '配置修订失败'
  }
}

async function openReport(row: Row) {
  report.value = null
  reportOpen.value = true
  try {
    const response = await request(`${ENDPOINT}/${row.id}/report`)
    if (!response.ok) throw new Error('路径报表读取失败')
    report.value = await response.json()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '路径报表读取失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('下钻路径列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '多维下钻列表读取失败'
  }
}

onMounted(reload)
</script>
