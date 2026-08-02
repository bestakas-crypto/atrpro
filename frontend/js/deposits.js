// 예금(현금) 추가/수정/삭제 모달.
import { api } from './api-client.js';
import { num } from './formatters.js';

export function openAddDepositModal(ctx) {
  const { el, state } = ctx;
  state.editingDepositId = null;
  el.modalDepositTitle.textContent = '예금 추가';
  el.depositAccountName.value = '';
  el.depositAmount.value = '';
  el.depositCurrency.value = 'KRW';
  el.btnDepositDelete.hidden = true;
  el.modalDeposit.hidden = false;
}

export function openEditDepositModal(ctx, deposit) {
  const { el, state } = ctx;
  state.editingDepositId = deposit.id;
  el.modalDepositTitle.textContent = '예금 수정';
  el.depositAccountName.value = deposit.account_name;
  el.depositAmount.value = deposit.amount;
  el.depositCurrency.value = deposit.currency;
  el.btnDepositDelete.hidden = false;
  el.modalDeposit.hidden = false;
}

export function closeDepositModal(ctx) {
  const { el, state } = ctx;
  el.modalDeposit.hidden = true;
  state.editingDepositId = null;
}

export async function handleDepositSave(ctx) {
  const { el, state } = ctx;
  const accountName = el.depositAccountName.value.trim();
  const amount = num(el.depositAmount.value);
  const currency = el.depositCurrency.value.trim() || 'KRW';

  if (!accountName) { ctx.showToast('계좌명을 입력하세요.'); return; }
  if (amount == null || amount < 0) { ctx.showToast('예금액을 올바르게 입력하세요.'); return; }

  try {
    if (state.editingDepositId) {
      await api.updateDeposit(state.editingDepositId, { account_name: accountName, amount, currency });
    } else {
      await api.createDeposit({ account_name: accountName, amount, currency });
    }
  } catch (e) {
    ctx.showToast('예금 저장 실패: ' + e.message);
    return;
  }
  closeDepositModal(ctx);
  ctx.refreshCurrentView();
  ctx.showToast('예금이 저장되었습니다.');
}

export function handleDepositDelete(ctx) {
  const { state } = ctx;
  const id = state.editingDepositId;
  if (!id) return;
  ctx.askConfirm('이 예금을 삭제합니다. 이 작업은 되돌릴 수 없습니다.', async () => {
    try {
      await api.deleteDeposit(id);
    } catch (e) {
      ctx.showToast('삭제 실패: ' + e.message);
      return;
    }
    closeDepositModal(ctx);
    ctx.refreshCurrentView();
    ctx.showToast('예금이 삭제되었습니다.');
  });
}
