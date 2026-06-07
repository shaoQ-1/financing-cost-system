from enum import Enum


class SessionStep(Enum):
    IDLE = "idle"

    # Task B
    B_UPLOAD_PDFS = "b_upload_pdfs"
    B_SELECT_PAGES = "b_select_pages"
    B_EXTRACTING = "b_extracting"
    B_EXTRACTION_DONE = "b_extraction_done"
    B_REVIEW = "b_review"
    B_RESULTS = "b_results"

    # Task A
    A_UPLOAD_LIABILITY = "a_upload_liability"
    A_CONFIGURE_RATES = "a_configure_rates"
    A_RUNNING = "a_running"
    A_RESULTS = "a_results"

    ERROR = "error"


VALID_TRANSITIONS = {
    SessionStep.IDLE: [SessionStep.B_UPLOAD_PDFS, SessionStep.A_UPLOAD_LIABILITY],
    SessionStep.B_UPLOAD_PDFS: [SessionStep.B_SELECT_PAGES],
    SessionStep.B_SELECT_PAGES: [SessionStep.B_EXTRACTING],
    SessionStep.B_EXTRACTING: [SessionStep.B_EXTRACTION_DONE, SessionStep.ERROR],
    SessionStep.B_EXTRACTION_DONE: [SessionStep.B_REVIEW, SessionStep.B_SELECT_PAGES],
    SessionStep.B_REVIEW: [SessionStep.B_RESULTS, SessionStep.B_SELECT_PAGES],
    SessionStep.B_RESULTS: [SessionStep.A_UPLOAD_LIABILITY, SessionStep.IDLE],
    SessionStep.A_UPLOAD_LIABILITY: [SessionStep.A_CONFIGURE_RATES],
    SessionStep.A_CONFIGURE_RATES: [SessionStep.A_RUNNING],
    SessionStep.A_RUNNING: [SessionStep.A_RESULTS, SessionStep.ERROR],
    SessionStep.A_RESULTS: [SessionStep.IDLE],
    SessionStep.ERROR: [SessionStep.IDLE, SessionStep.B_UPLOAD_PDFS, SessionStep.A_UPLOAD_LIABILITY],
}
