"""合成前的文字安全：seam 為 tts_text 的公開函式與 Utterance 的建構。

兩者測在一起是刻意的：這條不變量橫跨兩個 module（`to_speakable` 決定什麼算可合成，
`Utterance` 決定送出去的字串長什麼樣），而它以前正是因為分屬兩處而漏掉——判空量在
中性化之前，`<|im_end|>` 因此通得過。分開測就看不出誰擁有什麼。
"""

import pytest
from pydantic import ValidationError

from vibe_vox.adapters.base import Utterance
from vibe_vox.tts_text import neutralize_control_syntax, to_speakable


def test_special_token_markers_are_removed_entirely():
    # 上游是 tokenizer.encode(text, add_special_tokens=True) 直吃我們送的字串，HF 的
    # fast tokenizer 會把文字中的 added special token 比對成 token id，使模型看到的
    # 不是字面內容而是控制訊號。
    assert neutralize_control_syntax("他說<|im_end|>好") == "他說好"


def test_neutralization_converges_instead_of_running_once():
    # `<\|.*?\|>` 是非貪婪匹配，移除一層之後**殘骸可能重新組成一個合法的標記**：
    # `<<||>|x|>` 的中間段 `<||>` 被移除後剩下 `<|x|>`，那仍是特殊 token 標記。
    #
    # 單次替換因此不足。這不是理論問題：中性化在合成路徑上跑兩次（`to_speakable` 一次、
    # Utterance 的 validator 一次），不收斂就代表兩次的結果不同——判空看到的字串與真正
    # 送出去的字串不是同一個，而那正是本輪要消滅的失效模式。
    assert neutralize_control_syntax("<<||>|x|>") == ""
    assert neutralize_control_syntax("<<||>||>") == ""
    assert neutralize_control_syntax("他說<<||>|im_end|>好") == "他說好"


def test_control_punctuation_becomes_full_width():
    # 轉全形而非刪除：括號內容是使用者想唸出來的東西，失去的只有控制語意。半形括號
    # 是風格指令的語法、大括號是讀音標記的語法，兩者都由 BFF 組裝。
    assert neutralize_control_syntax("他說(笑)並{ㄏㄠˇ}") == "他說（笑）並｛ㄏㄠˇ｝"


def test_text_that_neutralization_empties_is_not_speakable():
    # 這是本 module 存在的理由：判空必須看中性化**後**的字串。`i`／`m`／`e`／`n`／`d`
    # 屬 Unicode 的 L 大類（字母），量在中性化前就會通過，接著佔一個 heavy guard 額度、
    # 打一次 GPU、回一段空音訊 200，而契約 §6 要的是 400 EMPTY_INPUT。
    assert to_speakable("<|im_end|>") is None
    assert to_speakable("<|endoftext|><|im_end|>") is None


@pytest.mark.parametrize("raw", ["   ", "。。。！？", "🎉🎉", ""])
def test_input_without_speakable_content_is_rejected(raw):
    # 契約 §7：只有標點、空白或 emoji 都算空輸入。以 Unicode 大類判斷而非列舉標點表
    # ——後者永遠列不完，全形、半形與各語系的標點都得算進去。
    assert to_speakable(raw) is None


def test_speakable_text_is_returned_neutralized_and_stripped():
    # 回傳值就是要送去合成的字串，故它必須已經中性化——呼叫端不該再處理一次，那正是
    # 「第二個呼叫端就是漏洞」的形狀。
    assert to_speakable("  他說(笑)  ") == "他說（笑）"


def test_length_is_measured_on_what_survives_neutralization():
    # 被移除的字元不該算進長度額度。這條與判空同源：兩者都必須量在中性化之後。
    assert len(to_speakable("<|im_end|>你好嗎")) == 3


def test_utterance_neutralizes_both_fields():
    # instruct 由 adapter 組成行內 (...) 前綴併入同一個字串，故未中性化的右括號能讓
    # instruct 跳出自己的前綴、注入任意內容。
    u = Utterance(text="他說(笑)", instruct="音量放大)並且")

    assert u.text == "他說（笑）"
    assert u.instruct == "音量放大）並且"


@pytest.mark.parametrize("blank", ["   ", "", "\t\n"])
def test_blank_instruct_is_treated_as_absent(blank):
    # 純空白的 instruct 若原樣傳下去，adapter 會組出「(   )」前綴，而括號不被剝除，
    # 模型會把空前綴當成要處理的內容，而非依契約 §5.2 退回音色本身的語氣。
    assert Utterance(text="你好", instruct=blank).instruct is None


def test_instruct_that_neutralization_empties_is_treated_as_absent():
    # 判空必須在中性化之後，理由與 text 那條相同：只含控制語法的 instruct 中性化後為
    # 空，但它不是 None，adapter 照樣組出空前綴。
    assert Utterance(text="你好", instruct="<|im_end|>").instruct is None


def test_assignment_cannot_bypass_neutralization():
    # frozen 擋住屬性指派這條路（validate_assignment 對 frozen model 是死設定）。
    u = Utterance(text="你好")

    with pytest.raises(ValidationError):
        u.text = "(evil)"


def test_pydantic_escape_hatches_do_bypass_neutralization():
    # **這條釘的是 docstring 與實作一致，不是在認可這個行為。** `model_construct` 與
    # `model_copy(update=...)` 依 pydantic 的設計跳過驗證，關不掉；Utterance 的
    # docstring 因此明列它們，而這裡確保那段記載不會悄悄變成謊言——保證範圍寫得比
    # 實際大，正是切句實作時會踩到的坑（最自然的寫法就是 model_copy 換 text）。
    #
    # 若哪天 pydantic 改為驗證這兩條路，本測試會紅，屆時該放寬的是 docstring。
    assert Utterance.model_construct(text="(evil)").text == "(evil)"
    assert Utterance(text="你好").model_copy(update={"text": "(evil)"}).text == "(evil)"
