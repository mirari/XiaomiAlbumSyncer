<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from 'primevue/button'
import Textarea from 'primevue/textarea'
import InputText from 'primevue/inputtext'
import Dialog from 'primevue/dialog'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import SettingSection from '@/components/settings/SettingSection.vue'
import { useToast } from 'primevue/usetoast'
import { useConfirm } from 'primevue/useconfirm'
import type { XiaomiAccountDto } from '@/__generated/model/dto'
import type { QrLoginSessionView } from '@/__generated/model/static'
import type { QrLoginStatus } from '@/__generated/model/enums'
import { storeToRefs } from 'pinia'
import { api } from '@/ApiInstance'
import { useAccountsStore } from '@/stores/accounts'
import { useAlbumsStore } from '@/stores/albums'
import { useCrontabsStore } from '@/stores/crontabs'

// 类型定义
type Account = XiaomiAccountDto['XiaomiAccountController/DEFAULT_XIAOMI_ACCOUNT']

const accountsStore = useAccountsStore()
const albumsStore = useAlbumsStore()
const crontabsStore = useCrontabsStore()

const { accounts, loading } = storeToRefs(accountsStore)
const saving = ref(false)
const showDialog = ref(false)
const isEditMode = ref(false)
const isInsecureContext = ref(false)

// 表单数据
const form = ref({
  id: 0,
  userId: '',
  nickname: '',
  passToken: '',
})

// 扫码登录
const qrDialog = ref(false)
const qrSession = ref<QrLoginSessionView | null>(null)
const qrStatus = ref<QrLoginStatus | 'CREATING'>('CREATING')
const qrError = ref('')
let qrTimer: ReturnType<typeof setInterval> | undefined

const { t } = useI18n()
const toast = useToast()
const confirm = useConfirm()

onMounted(() => {
  try {
    isInsecureContext.value = typeof window !== 'undefined' && !window.isSecureContext
  } catch {
    isInsecureContext.value = false
  }
  accountsStore.fetchAccounts()
})

onUnmounted(() => {
  stopQrPolling()
})

watch(qrDialog, (visible) => {
  if (!visible) stopQrPolling()
})

function stopQrPolling() {
  if (qrTimer !== undefined) {
    clearInterval(qrTimer)
    qrTimer = undefined
  }
}

async function openQrDialog() {
  qrDialog.value = true
  await refreshQrSession()
}

async function refreshQrSession() {
  stopQrPolling()
  qrSession.value = null
  qrStatus.value = 'CREATING'
  qrError.value = ''
  try {
    qrSession.value = await api.qrLoginController.create()
    qrStatus.value = 'WAITING'
    startQrPolling()
  } catch (e) {
    qrStatus.value = 'FAILED'
    qrError.value = e instanceof Error ? e.message : String(e)
  }
}

function startQrPolling() {
  qrTimer = setInterval(async () => {
    const session = qrSession.value
    if (!session) return
    try {
      const result = await api.qrLoginController.status({ sessionId: session.sessionId })
      qrStatus.value = result.status
      if (result.status === 'WAITING') return

      stopQrPolling()
      if (result.status === 'SUCCESS') {
        toast.add({
          severity: 'success',
          summary: t('common.toast.success'),
          detail: t('tokens.account.qrLoggedIn', { name: result.nickname || result.userId }),
          life: 3000,
        })
        qrDialog.value = false
        await Promise.all([
          accountsStore.refreshAccounts(),
          albumsStore.refreshAlbums(),
          crontabsStore.refreshCrontabs(),
        ])
      } else if (result.status === 'FAILED') {
        qrError.value = result.error ?? ''
      }
    } catch (e) {
      stopQrPolling()
      qrStatus.value = 'FAILED'
      qrError.value = e instanceof Error ? e.message : String(e)
    }
  }, 2000)
}

function openCreateDialog() {
  isEditMode.value = false
  form.value = { id: 0, userId: '', nickname: '', passToken: '' }
  showDialog.value = true
}

function openEditDialog(account: Account) {
  isEditMode.value = true
  // 根据 update 接口定义，passToken 是必填的
  form.value = {
    id: account.id,
    userId: account.userId,
    nickname: account.nickname,
    passToken: '',
  }
  showDialog.value = true
}

async function onSave() {
  if (!form.value.userId) {
    toast.add({
      severity: 'warn',
      summary: t('common.toast.warn'),
      detail: t('tokens.account.enterUserId'),
      life: 2500,
    })
    return
  }
  if (!form.value.passToken) {
    toast.add({
      severity: 'warn',
      summary: t('common.toast.warn'),
      detail: t('tokens.account.enterPassToken'),
      life: 2500,
    })
    return
  }

  const finalNickname = form.value.nickname || form.value.userId

  try {
    saving.value = true
    if (isEditMode.value) {
      await accountsStore.updateAccount(form.value.id, {
        userId: form.value.userId,
        nickname: finalNickname,
        passToken: form.value.passToken,
      })
      toast.add({
        severity: 'success',
        summary: t('common.toast.success'),
        detail: t('tokens.account.updated'),
        life: 2000,
      })
    } else {
      await accountsStore.createAccount({
        userId: form.value.userId,
        nickname: finalNickname,
        passToken: form.value.passToken,
      })
      toast.add({
        severity: 'success',
        summary: t('common.toast.success'),
        detail: t('tokens.account.added'),
        life: 2000,
      })
    }
    showDialog.value = false
    await Promise.all([albumsStore.refreshAlbums(), crontabsStore.refreshCrontabs()])
  } catch (e) {
    const detail = e instanceof Error ? e.message : String(e) || t('common.toast.saveFailed')
    toast.add({ severity: 'error', summary: t('common.toast.error'), detail, life: 3000 })
  } finally {
    saving.value = false
  }
}

function confirmDelete(account: Account) {
  confirm.require({
    message: t('tokens.account.confirmDelete', { name: account.nickname || account.userId }),
    header: t('tokens.account.confirmDeleteTitle'),
    acceptClass: 'p-button-danger',
    rejectLabel: t('common.action.cancel'),
    acceptLabel: t('tokens.account.confirmDeleteAccept'),
    accept: async () => {
      try {
        await accountsStore.deleteAccount(account.id)
        toast.add({
          severity: 'success',
          summary: t('common.toast.success'),
          detail: t('tokens.account.deleted'),
          life: 2000,
        })
        await Promise.all([albumsStore.refreshAlbums(), crontabsStore.refreshCrontabs()])
      } catch (e) {
        const detail =
          e instanceof Error ? e.message : String(e) || t('tokens.account.deleteFailed')
        toast.add({ severity: 'error', summary: t('common.toast.error'), detail, life: 3000 })
      }
    },
  })
}
</script>

<template>
  <SettingSection :title="t('tokens.account.title')" :description="t('tokens.account.description')">
    <template #actions>
      <div class="flex gap-2">
        <Button
          :label="t('tokens.account.qrAdd')"
          icon="pi pi-qrcode"
          size="small"
          severity="secondary"
          @click="openQrDialog"
        />
        <Button
          :label="t('tokens.account.add')"
          icon="pi pi-plus"
          size="small"
          severity="primary"
          @click="openCreateDialog"
        />
      </div>
    </template>

    <div
      v-if="isInsecureContext"
      class="mb-4 rounded-md bg-red-50 dark:bg-red-950/40 text-red-700 dark:text-red-300 text-xs px-3 py-2 ring-1 ring-red-200 dark:ring-red-900/70"
    >
      {{ t('tokens.account.insecureWarning') }}
    </div>

    <DataTable :value="accounts" :loading="loading" size="small" class="text-sm">
      <template #empty>{{ t('tokens.account.empty') }}</template>
      <Column field="nickname" :header="t('tokens.account.nickname')"></Column>
      <Column field="userId" :header="t('tokens.account.userId')"></Column>
      <Column :header="t('tokens.table.actions')" :style="{ width: '10rem' }">
        <template #body="slotProps">
          <div class="flex gap-2">
            <Button
              icon="pi pi-pencil"
              text
              rounded
              severity="secondary"
              @click="openEditDialog(slotProps.data)"
            />
            <Button
              icon="pi pi-trash"
              text
              rounded
              severity="danger"
              @click="confirmDelete(slotProps.data)"
            />
          </div>
        </template>
      </Column>
    </DataTable>
  </SettingSection>

  <!-- 添加/编辑 弹窗 -->
  <Dialog
    v-model:visible="showDialog"
    modal
    :header="isEditMode ? t('tokens.account.edit') : t('tokens.account.add')"
    class="w-full sm:w-[480px]"
  >
    <div class="flex flex-col gap-4 pt-2">
      <div class="flex gap-3">
        <div class="flex-1">
          <label class="block text-sm font-medium text-slate-700 dark:text-slate-200 mb-1">{{
            t('tokens.account.userId')
          }}</label>
          <InputText
            v-model="form.userId"
            :placeholder="t('tokens.account.userIdPlaceholder')"
            class="w-full"
          />
        </div>
        <div class="flex-1">
          <label class="block text-sm font-medium text-slate-700 dark:text-slate-200 mb-1"
            >{{ t('tokens.account.nickname') }} ({{ t('common.field.optional') }})</label
          >
          <InputText
            v-model="form.nickname"
            :placeholder="t('tokens.account.nicknamePlaceholder')"
            class="w-full"
          />
        </div>
      </div>

      <div>
        <label class="block text-sm font-medium text-slate-700 dark:text-slate-200 mb-1">{{
          t('tokens.account.passToken')
        }}</label>
        <Textarea
          v-model="form.passToken"
          rows="5"
          :placeholder="
            isEditMode
              ? t('tokens.account.passTokenPlaceholderEdit')
              : t('tokens.account.passTokenPlaceholder')
          "
          class="w-full"
        />
        <p v-if="isEditMode" class="text-xs text-slate-500 dark:text-slate-400 mt-1">
          {{ t('tokens.account.passTokenNote') }}
        </p>
      </div>

      <div v-if="isInsecureContext" class="text-xs text-red-600 dark:text-red-300">
        {{ t('tokens.account.insecureShort') }}
      </div>
    </div>

    <template #footer>
      <div class="flex items-center justify-end gap-2 w-full mt-4">
        <Button
          :label="t('common.action.cancel')"
          severity="secondary"
          text
          @click="showDialog = false"
        />
        <Button
          :label="t('common.action.save')"
          severity="primary"
          :loading="saving"
          @click="onSave"
        />
      </div>
    </template>
  </Dialog>

  <!-- 扫码登录 弹窗 -->
  <Dialog
    v-model:visible="qrDialog"
    modal
    :header="t('tokens.account.qrTitle')"
    class="w-full sm:w-[400px]"
  >
    <div class="flex flex-col items-center gap-4 pt-2">
      <div
        class="w-[240px] h-[240px] flex items-center justify-center rounded-lg bg-slate-100 dark:bg-slate-800 overflow-hidden"
      >
        <i v-if="qrStatus === 'CREATING'" class="pi pi-spin pi-spinner text-3xl text-slate-400"></i>
        <img
          v-else-if="qrSession"
          :src="qrSession.qrUrl"
          :alt="t('tokens.account.qrTitle')"
          class="w-full h-full object-contain"
          :class="{ 'opacity-30': qrStatus === 'EXPIRED' }"
        />
        <i v-else class="pi pi-exclamation-triangle text-3xl text-red-400"></i>
      </div>

      <div class="text-center text-sm">
        <p v-if="qrStatus === 'CREATING'" class="text-slate-500 dark:text-slate-400">
          {{ t('tokens.account.qrGenerating') }}
        </p>
        <template v-else-if="qrStatus === 'WAITING'">
          <p class="font-medium text-slate-700 dark:text-slate-200">
            {{ t('tokens.account.qrWaiting') }}
          </p>
          <p class="text-xs text-slate-500 dark:text-slate-400 mt-1">
            {{ t('tokens.account.qrTip') }}
          </p>
        </template>
        <p v-else-if="qrStatus === 'EXPIRED'" class="text-amber-600 dark:text-amber-400">
          {{ t('tokens.account.qrExpired') }}
        </p>
        <p v-else-if="qrStatus === 'FAILED'" class="text-red-600 dark:text-red-400">
          {{ t('tokens.account.qrFailed') }}<template v-if="qrError">：{{ qrError }}</template>
        </p>
      </div>
    </div>

    <template #footer>
      <div class="flex items-center justify-end gap-2 w-full mt-4">
        <Button
          :label="t('common.action.cancel')"
          severity="secondary"
          text
          @click="qrDialog = false"
        />
        <Button
          v-if="qrStatus === 'EXPIRED' || qrStatus === 'FAILED'"
          :label="t('tokens.account.qrRetry')"
          severity="primary"
          @click="refreshQrSession"
        />
      </div>
    </template>
  </Dialog>
</template>
