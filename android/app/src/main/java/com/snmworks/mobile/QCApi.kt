package com.snmworks.mobile

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.OutputStreamWriter
import java.net.HttpURLConnection
import java.net.URL

data class JobOption(
    val id: String,
    val jobNo: String,
    val product: String,
    val status: String
)

data class QCOptions(
    val jobs: List<JobOption>,
    val stages: List<String>,
    val limitKinds: List<String>
)

data class QCCheckRequest(
    val jobId: String?,
    val stage: String,
    val parameter: String,
    val unit: String?,
    val limitType: String,
    val specValue: Double,
    val actual: Double?
)

data class QCCheckResponse(
    val id: String,
    val checkNo: String,
    val verdict: String,
    val parameter: String,
    val specValue: Double,
    val actual: Double?,
    val unit: String?
)

object QCApi {
    private const val BASE_URL = "http://10.18.221.141:8080"

    suspend fun fetchOptions(accessToken: String): Result<QCOptions> = withContext(Dispatchers.IO) {
        try {
            val url = URL("$BASE_URL/api/v1/qc/options")
            val conn = (url.openConnection() as HttpURLConnection).apply {
                requestMethod = "GET"
                setRequestProperty("Authorization", "Bearer $accessToken")
                setRequestProperty("Accept", "application/json")
                connectTimeout = 10000
                readTimeout = 10000
            }

            val responseCode = conn.responseCode
            if (responseCode !in 200..299) {
                val errStream = conn.errorStream ?: conn.inputStream
                val errText = errStream?.bufferedReader()?.use { it.readText() } ?: "HTTP $responseCode"
                return@withContext Result.failure(Exception("Failed to load QC options: $errText"))
            }

            val body = conn.inputStream.bufferedReader().use { it.readText() }
            val json = JSONObject(body)

            val jobsList = mutableListOf<JobOption>()
            val jobsArray = json.optJSONArray("jobs") ?: JSONArray()
            for (i in 0 until jobsArray.length()) {
                val item = jobsArray.getJSONObject(i)
                jobsList.add(
                    JobOption(
                        id = item.optString("id"),
                        jobNo = item.optString("job_no"),
                        product = item.optString("product"),
                        status = item.optString("status")
                    )
                )
            }

            val stagesList = mutableListOf<String>()
            val stagesArray = json.optJSONArray("stages") ?: JSONArray()
            for (i in 0 until stagesArray.length()) {
                stagesList.add(stagesArray.getString(i))
            }

            val limitKindsList = mutableListOf<String>()
            val limitKindsArray = json.optJSONArray("limit_kinds") ?: JSONArray()
            for (i in 0 until limitKindsArray.length()) {
                limitKindsList.add(limitKindsArray.getString(i))
            }

            Result.success(QCOptions(jobsList, stagesList, limitKindsList))
        } catch (e: Exception) {
            Result.failure(e)
        }
    }

    suspend fun submitCheck(
        request: QCCheckRequest,
        accessToken: String
    ): Result<QCCheckResponse> = withContext(Dispatchers.IO) {
        try {
            val url = URL("$BASE_URL/api/v1/qc/checks")
            val conn = (url.openConnection() as HttpURLConnection).apply {
                requestMethod = "POST"
                setRequestProperty("Authorization", "Bearer $accessToken")
                setRequestProperty("Content-Type", "application/json")
                setRequestProperty("Accept", "application/json")
                doOutput = true
                connectTimeout = 10000
                readTimeout = 10000
            }

            val payload = JSONObject().apply {
                if (!request.jobId.isNullOrBlank()) {
                    put("job_id", request.jobId)
                }
                put("stage", request.stage)
                put("parameter", request.parameter)
                if (!request.unit.isNullOrBlank()) {
                    put("unit", request.unit)
                }
                put("limit_type", request.limitType)
                put("spec_value", request.specValue)
                if (request.actual != null) {
                    put("actual", request.actual)
                }
            }

            OutputStreamWriter(conn.outputStream).use { it.write(payload.toString()) }

            val responseCode = conn.responseCode
            if (responseCode !in 200..299) {
                val errStream = conn.errorStream ?: conn.inputStream
                val errText = errStream?.bufferedReader()?.use { it.readText() } ?: "HTTP $responseCode"
                return@withContext Result.failure(Exception("Submission failed ($responseCode): $errText"))
            }

            val body = conn.inputStream.bufferedReader().use { it.readText() }
            val json = JSONObject(body)

            val checkResponse = QCCheckResponse(
                id = json.optString("id"),
                checkNo = json.optString("check_no", "Q-XXXX"),
                verdict = json.optString("verdict", "UNKNOWN"),
                parameter = request.parameter,
                specValue = request.specValue,
                actual = request.actual,
                unit = request.unit
            )

            Result.success(checkResponse)
        } catch (e: Exception) {
            Result.failure(e)
        }
    }
}
