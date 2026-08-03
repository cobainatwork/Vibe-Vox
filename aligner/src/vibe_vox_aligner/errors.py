"""端點層錯誤。

種類多但處理方式一致（都是映射成同一個 JSON 信封），故用單一類別攜帶狀態碼
與 code，而非為每種錯誤各開一個 Exception 子類。
"""


class AlignerError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
