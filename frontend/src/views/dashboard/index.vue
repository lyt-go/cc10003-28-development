<template>
  <section class="page" data-module="dashboard">
    <header class="page-head">
      <div>
        <h2>看板配置管理</h2>
        <p class="page-desc">配置数据看板页面，发布时只能引用同版本已启用的下钻路径层级，进入后按默认展开层展示。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记看板页面</button>
        <button class="btn" type="button" @click="exportRows">导出看板配置清单</button>
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
          <th>引用状态</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td>
            <span
              class="badge"
              :class="row['引用状态'] === '生效' ? 'ok' : row['引用状态'] === '失效' ? 'bad' : 'muted'"
            >{{ row['引用状态'] ?? '未引用' }}</span>
            <div v-if="row['失效原因']" class="page-desc">{{ row['失效原因'] }}<template v-if="row['新可用版本']">；新版本：{{ row['新可用版本'] }}</template></div>
          </td>
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
            <button class="link" type="button" @click="openEnter(row)">进入看板</button>
            <button class="link" type="button" @click="openReport(row)">详情报表</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 2" class="empty-state">暂无看板配置数据，可先登记看板页面</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条看板配置记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <!-- 发布看板：选择同路径的已启用下钻版本 -->
    <Modal :open="publishOpen" title="发布看板：选择已启用的下钻路径版本" @close="publishOpen = false">
      <p class="page-desc">守卫令牌 publish_guard：<code>{{ guard }}</code>；只有「已启用」版本可被引用，校验不通过会阻止发布并保留旧版本。</p>
      <div v-if="!versionOptions.length" class="empty-state">当前没有已启用的下钻路径版本，请先到多维下钻启用路径</div>
      <div
        v-for="option in versionOptions"
        :key="option.id"
        class="version-option"
        :class="{ active: selectedVersionId === option.id }"
        @click="selectedVersionId = option.id"
      >
        <strong>{{ option['路径编号'] }} · {{ option['版本号'] }}</strong>
        <dl class="kv-grid" style="margin-top: 6px">
          <dt>每层维度名</dt><dd>{{ option['每层维度名'].join(' → ') }}</dd>
          <dt>路径深度</dt><dd>{{ option['路径深度'] }}</dd>
          <dt>默认展开层</dt><dd>{{ option['默认展开层'] }}（展开到 {{ option['每层维度名'][option['默认展开层'] - 1] }}）</dd>
          <dt>每层度量</dt><dd>{{ option['每层度量'].map((layer: string[]) => layer.join('/')).join(' ｜ ') }}</dd>
        </dl>
      </div>
      <p v-if="publishHint" class="error-text">{{ publishHint }}</p>
      <template #footer>
        <button class="btn ghost" type="button" @click="publishOpen = false">取消</button>
        <button class="btn primary" type="button" :disabled="!selectedVersionId" @click="confirmPublish">确认发布</button>
      </template>
    </Modal>

    <!-- 进入看板：默认展开层视图 -->
    <Modal :open="enterOpen" title="进入看板：默认展开层视图" @close="enterOpen = false">
      <div v-if="enterView">
        <template v-if="enterView['提示']">
          <p class="page-desc">{{ enterView['提示'] }}</p>
        </template>
        <template v-else>
          <p>
            引用 <strong>{{ enterView['路径编号'] }} {{ enterView['版本号'] }}</strong>
            <span
              class="badge"
              :class="enterView['引用状态'] === '生效' ? 'ok' : 'bad'"
              style="margin-left: 6px"
            >{{ enterView['引用状态'] }}</span>
          </p>
          <p v-if="enterView['失效原因']" class="error-text">
            失效来源：{{ enterView['失效原因'] }}<template v-if="enterView['新可用版本']">；新可用版本：{{ enterView['新可用版本'] }}</template>
          </p>
          <dl class="kv-grid">
            <dt>起始汇总维度</dt><dd>{{ enterView['汇总维度'] }}</dd>
            <dt>默认展开层</dt><dd>第 {{ enterView['默认展开层'] }} 层（{{ enterView['展开到维度'] }}）</dd>
            <dt>路径深度</dt><dd>{{ enterView['路径深度'] }}</dd>
            <dt>每层度量</dt><dd>{{ enterView['每层度量'].map((layer: string[]) => layer.join('/')).join(' ｜ ') }}</dd>
          </dl>
          <p class="section-title">层级展开效果</p>
          <div class="enter-layer">
            <template v-for="(dim, index) in enterView['全部层级']" :key="dim">
              <span class="chip" :class="{ expanded: index < enterView['默认展开层'] }">
                {{ index + 1 }}. {{ dim }}
              </span>
              <span v-if="index < enterView['全部层级'].length - 1">→</span>
            </template>
          </div>
        </template>
      </div>
    </Modal>

    <!-- 看板详情报表：追到路径版本、层级、默认展开层与审计快照 -->
    <Modal :open="reportOpen" title="看板详情报表（版本与审计追溯）" width="820px" @close="reportOpen = false">
      <div v-if="report">
        <p class="section-title">看板</p>
        <dl class="kv-grid">
          <dt>看板编号</dt><dd>{{ report['看板']['看板编号'] }}</dd>
          <dt>状态</dt><dd>{{ report['看板']['status'] }}</dd>
          <dt>引用状态</dt><dd>{{ report['看板']['引用状态'] ?? '未引用' }}</dd>
          <dt>失效来源</dt><dd>{{ report['看板']['失效原因'] ?? '—' }}</dd>
          <dt>新可用版本</dt><dd>{{ report['看板']['新可用版本'] ?? '—' }}</dd>
        </dl>

        <template v-if="report['当前引用']">
          <p class="section-title">当前引用（冻结快照）</p>
          <dl class="kv-grid">
            <dt>路径编号 / 版本</dt><dd>{{ report['当前引用']['路径编号'] }} · {{ report['当前引用']['版本号'] }}</dd>
            <dt>下钻层级</dt><dd>{{ report['当前引用']['下钻层级'].join(' → ') }}</dd>
            <dt>路径深度</dt><dd>{{ report['当前引用']['路径深度'] }}</dd>
            <dt>默认展开层</dt><dd>{{ report['当前引用']['默认展开层'] }}</dd>
            <dt>每层度量</dt><dd>{{ report['当前引用']['每层度量'].map((layer: string[]) => layer.join('/')).join(' ｜ ') }}</dd>
            <dt>publish_guard</dt><dd><code>{{ report['当前引用']['publish_guard'] }}</code></dd>
          </dl>
        </template>

        <template v-if="report['路径版本']">
          <p class="section-title">路径版本</p>
          <dl class="kv-grid">
            <dt>版本号 / 状态</dt><dd>{{ report['路径版本']['版本号'] }} · {{ report['路径版本']['status'] }}</dd>
            <dt>启用时间</dt><dd>{{ report['路径版本']['启用时间'] }}</dd>
            <dt>停用时间</dt><dd>{{ report['路径版本']['停用时间'] ?? '—' }}</dd>
          </dl>
        </template>

        <p class="section-title">审计记录（与快照同一版本）</p>
        <div v-for="log in report['审计记录']" :key="log.id" class="audit-item">
          <strong>{{ log['动作'] }} · {{ log['结果'] }}</strong>
          <span class="page-desc" style="margin-left: 8px">{{ log['时间'] }} · {{ log['说明'] }}</span>
          <pre>{{ JSON.stringify(log['快照'], null, 2) }}</pre>
        </div>
        <p v-if="!report['审计记录'].length" class="empty-state">暂无审计记录</p>
      </div>
    </Modal>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import Modal from '@/components/Modal.vue'
import { request } from '@/api/client'

type Row = Record<string, any>

const ENDPOINT = '/api/dashboard'
const columns = ['看板编号', '看板名称', '包含指标', '引用路径编号', '引用路径版本', '默认展开层']
const actions = ['提交看板评审', '发布看板', '下线看板']
const statuses = ['草稿', '看板评审中', '已发布', '已下线']
const stats = [{ label: '看板页面总数', value: 0 }, { label: '已发布看板', value: 0 }, { label: '看板评审中', value: 0 }]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)

const publishOpen = ref(false)
const publishTarget = ref<Row | null>(null)
const versionOptions = ref<Row[]>([])
const selectedVersionId = ref<number | null>(null)
const publishHint = ref('')
const guard = ref('')

const enterOpen = ref(false)
const enterView = ref<Row | null>(null)

const reportOpen = ref(false)
const report = ref<Row | null>(null)

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '看板页面登记入口尚未接入审批流'
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  if (action === '发布看板') {
    publishTarget.value = row
    publishHint.value = ''
    selectedVersionId.value = null
    publishOpen.value = true
    try {
      const response = await request(`${ENDPOINT}/enabled-versions`)
      const payload = await response.json()
      versionOptions.value = payload.items ?? []
      guard.value = payload.publish_guard ?? ''
    } catch (error) {
      versionOptions.value = []
      publishHint.value = error instanceof Error ? error.message : '已启用版本读取失败'
    }
    return
  }
  await postAction(row.id, { action })
}

async function confirmPublish() {
  if (!publishTarget.value || selectedVersionId.value === null) return
  publishHint.value = ''
  await postAction(publishTarget.value.id, {
    action: '发布看板',
    版本id: selectedVersionId.value,
    publish_guard: guard.value,
  })
  if (!errorMessage.value) {
    publishOpen.value = false
  }
}

async function postAction(id: number, body: Record<string, unknown>) {
  try {
    const response = await request(`${ENDPOINT}/${id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: body }),
    })
    const payload = await response.json()
    if (!response.ok || payload.ok === false) {
      throw new Error(payload.message ?? '看板配置动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '看板配置操作失败'
  }
}

async function openEnter(row: Row) {
  enterView.value = null
  enterOpen.value = true
  try {
    const response = await request(`${ENDPOINT}/${row.id}/enter`)
    if (!response.ok) throw new Error('看板进入视图读取失败')
    enterView.value = await response.json()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '看板进入视图读取失败'
  }
}

async function openReport(row: Row) {
  report.value = null
  reportOpen.value = true
  try {
    const response = await request(`${ENDPOINT}/${row.id}/report`)
    if (!response.ok) throw new Error('看板详情报表读取失败')
    report.value = await response.json()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '看板详情报表读取失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('看板页面列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '看板配置列表读取失败'
  }
}

onMounted(reload)
</script>
