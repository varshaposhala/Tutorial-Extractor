# Learning Resource Extractor

Web app that logs into `learning.ccbp.in` with phone + OTP, collects **TUTORIAL** units from `course_details/v4`, then copies those learning resources from NKB admin. `DEFAULT_QUESTIONS` steps are skipped. CSV and Excel are downloaded at the end.

Each person uses their own learning-portal mobile number and admin username/password. Credentials are not stored.


On the page:

1. Paste a course ID or a `learning.ccbp.in/course?c_id=...` URL
2. Enter the mobile number used for learning.ccbp.in
3. Enter NKB admin username and password
4. Click **Extract content**
5. When asked, enter the OTP from your phone
6. Download Excel and CSV when it finishes

Optional extra resource IDs can still be pasted if you want to extract IDs that are not TUTORIAL units.

