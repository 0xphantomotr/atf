from fastapi import HTTPException, status


class NotImplementedYet(HTTPException):
    def __init__(self, detail: str = "Ky funksion nuk është implementuar ende.") -> None:
        super().__init__(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=detail)

