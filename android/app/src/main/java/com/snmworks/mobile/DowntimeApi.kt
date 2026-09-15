package com.snmworks.mobile

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.OutputStreamWriter
import java.net.HttpURLConnection
import java.net.URL

data class DowntimeJobOption(
    val id: String,
    val jobNo: String,
    val product: String,
    val machine: String?
)

data class DowntimeOptions(
    val machines: List<String>,
    val shifts: List<String>,
    val reasons: List<String>,
    val jobs: List<DowntimeJobOption>
)

data class DowntimeLogRequest(
    val machine: String,
    val shift: String,
    val reason: String,
    val minutes: Double,
    val jobId: String?,
    val remarks: String?
)

data class DowntimeLogResponse(
    val id: String,
    val logNo: String,
    val machine: String,
    val shift: String,
    val reason: String,
    val minutes: Double,
    val formattedDuration: String,
    val remarks: String?
)

object DowntimeApi {
    private const val BASE_URL = "http://10.18.221.141:8080"

    suspend fun fetchOptions(accessToken: String): Result<DowntimeOptions> = withContext(Dispatchers.IO) {
        try {
            val url = URL("$BASE_URL/api/v1/downtime/options")
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
                return@withContext Result.failure(Exception("Failed to load downtime options: $errText"))
            }

            val body = conn.inputStream.bufferedReader().use { it.readText() }
            val json = JSONObject(body)

            val machinesList = mutableListOf<String>()
            val machinesArray = json.optJSONArray("machines") ?: JSONArray()
            for (i in 0 until machinesArray.length()) {
                machinesList.add(machinesArray.getString(i))
            }

            val shiftsList = mutableListOf<String>()
            val shiftsArray = json.optJSONArray("shifts") ?: JSONArray()
            for (i in 0 until shiftsArray.length()) {
                shiftsList.add(shiftsArray.getString(i))
            }

            val reasonsList = mutableListOf<String>()
            val reasonsArray = json.optJSONArray("reasons") ?: JSONArray()
            for (i in 0 until reasonsArray.length()) {
                reasonsList.add(reasonsArray.getString(i))
            }

            val jobsList = mutableListOf<DowntimeJobOption>()
            val jobsArray = json.optJSONArray("jobs") ?: JSONArray()
            for (i in 0 until jobsArray.length()) {
                val item = jobsArray.getJSONObject(i)
                jobsList.add(
                    DowntimeJobOption(
                        id = item.optString("id"),
                        jobNo = item.optString("job_no"),
                        product = item.optString("product"),
                        machine = if (item.isNull("machine")) null else item.optString("machine")
                    )
                )
            }

            Result.success(DowntimeOptions(machinesList, shiftsList, reasonsList, jobsList))
        } catch (e: Exception) {
            Result.failure(e)
        }
    }

    suspend fun submitLog(
        request: DowntimeLogRequest,
        accessToken: String
    ): Result<DowntimeLogResponse> = withContext(Dispatchers.IO) {
        try {
            val url = URL("$BASE_URL/api/v1/downtime/logs")
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
                put("machine", request.machine)
                put("shift", request.shift)
                put("reason", request.reason)
                put("minutes", request.minutes)
                if (!request.jobId.isNullOrBlank()) {
                    put("job_id", request.jobId)
                }
                if (!request.remarks.isNullOrBlank()) {
                    put("remarks", request.remarks)
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

            val logResponse = DowntimeLogResponse(
                id = json.optString("id"),
                logNo = json.optString("log_no", "DT-XXXX"),
                machine = json.optString("machine", request.machine),
                shift = json.optString("shift", request.shift),
                reason = json.optString("reason", request.reason),
                minutes = json.optDouble("minutes", request.minutes),
                formattedDuration = json.optString("formatted_duration", "${request.minutes.toInt()}m"),
                remarks = if (json.isNull("remarks")) null else json.optString("remarks")
            )

            Result.success(logResponse)
        } catch (e: Exception) {
            Result.failure(e)
        }
    }
}
