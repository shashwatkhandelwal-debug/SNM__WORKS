package com.snmworks.mobile

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.OutputStreamWriter
import java.net.HttpURLConnection
import java.net.URL

data class MaterialJobOption(
    val id: String,
    val jobNo: String,
    val product: String,
    val status: String
)

data class ApprovedYarnLotOption(
    val id: String,
    val lotNo: String,
    val supplierLotNo: String?,
    val supplierName: String?,
    val yarnType: String,
    val denier: Double?,
    val qtyRemaining: Double,
    val unit: String,
    val storageLocation: String?
)

data class MaterialIssueOptions(
    val jobs: List<MaterialJobOption>,
    val approvedLots: List<ApprovedYarnLotOption>
)

data class MaterialIssueRequest(
    val jobId: String,
    val yarnLotId: String,
    val qtyIssued: Double,
    val unit: String,
    val remarks: String?
)

data class MaterialIssueResponse(
    val id: String,
    val issueNo: String,
    val jobId: String,
    val yarnLotId: String,
    val qtyIssued: Double,
    val unit: String,
    val remainingLotQty: Double,
    val remarks: String?
)

data class MaterialIssueListItem(
    val id: String,
    val issueNo: String,
    val issuedDate: String?,
    val jobId: String?,
    val jobNo: String?,
    val yarnLotId: String?,
    val lotNo: String?,
    val qtyIssued: Double,
    val unit: String,
    val remarks: String?
)

object MaterialsApi {
    private val BASE_URL: String
        get() = BuildConfig.API_BASE_URL

    suspend fun fetchOptions(accessToken: String): Result<MaterialIssueOptions> = withContext(Dispatchers.IO) {
        try {
            val url = URL("$BASE_URL/api/v1/materials/issue/options")
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
                return@withContext Result.failure(Exception("Failed to load material options: $errText"))
            }

            val body = conn.inputStream.bufferedReader().use { it.readText() }
            val json = JSONObject(body)

            val jobsList = mutableListOf<MaterialJobOption>()
            val jobsArray = json.optJSONArray("jobs") ?: JSONArray()
            for (i in 0 until jobsArray.length()) {
                val item = jobsArray.getJSONObject(i)
                jobsList.add(
                    MaterialJobOption(
                        id = item.optString("id"),
                        jobNo = item.optString("job_no"),
                        product = item.optString("product"),
                        status = item.optString("status")
                    )
                )
            }

            val lotsList = mutableListOf<ApprovedYarnLotOption>()
            val lotsArray = json.optJSONArray("approved_lots") ?: JSONArray()
            for (i in 0 until lotsArray.length()) {
                val item = lotsArray.getJSONObject(i)
                lotsList.add(
                    ApprovedYarnLotOption(
                        id = item.optString("id"),
                        lotNo = item.optString("lot_no"),
                        supplierLotNo = if (item.isNull("supplier_lot_no")) null else item.optString("supplier_lot_no"),
                        supplierName = if (item.isNull("supplier_name")) null else item.optString("supplier_name"),
                        yarnType = item.optString("yarn_type"),
                        denier = if (item.isNull("denier")) null else item.optDouble("denier"),
                        qtyRemaining = item.optDouble("qty_remaining", 0.0),
                        unit = item.optString("unit", "kg"),
                        storageLocation = if (item.isNull("storage_location")) null else item.optString("storage_location")
                    )
                )
            }

            Result.success(MaterialIssueOptions(jobsList, lotsList))
        } catch (e: Exception) {
            Result.failure(e)
        }
    }

    suspend fun submitIssue(
        request: MaterialIssueRequest,
        accessToken: String
    ): Result<MaterialIssueResponse> = withContext(Dispatchers.IO) {
        try {
            val url = URL("$BASE_URL/api/v1/materials/issue")
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
                put("job_id", request.jobId)
                put("yarn_lot_id", request.yarnLotId)
                put("qty_issued", request.qtyIssued)
                put("unit", request.unit)
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

            val issueResponse = MaterialIssueResponse(
                id = json.optString("id"),
                issueNo = json.optString("issue_no", "ISS-XXXX"),
                jobId = json.optString("job_id", request.jobId),
                yarnLotId = json.optString("yarn_lot_id", request.yarnLotId),
                qtyIssued = json.optDouble("qty_issued", request.qtyIssued),
                unit = json.optString("unit", request.unit),
                remainingLotQty = json.optDouble("remaining_lot_qty", 0.0),
                remarks = if (json.isNull("remarks")) null else json.optString("remarks")
            )

            Result.success(issueResponse)
        } catch (e: Exception) {
            Result.failure(e)
        }
    }

    suspend fun fetchRecentIssues(accessToken: String, limit: Int = 50): Result<List<MaterialIssueListItem>> = withContext(Dispatchers.IO) {
        try {
            val url = URL("$BASE_URL/api/v1/materials/issues?limit=$limit")
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
                return@withContext Result.failure(Exception("Failed to load material issue history: $errText"))
            }

            val body = conn.inputStream.bufferedReader().use { it.readText() }
            val json = JSONObject(body)
            val issuesArray = json.optJSONArray("issues") ?: JSONArray()
            val list = mutableListOf<MaterialIssueListItem>()

            for (i in 0 until issuesArray.length()) {
                val item = issuesArray.getJSONObject(i)
                list.add(
                    MaterialIssueListItem(
                        id = item.optString("id"),
                        issueNo = item.optString("issue_no"),
                        issuedDate = if (item.isNull("issued_date")) null else item.optString("issued_date"),
                        jobId = if (item.isNull("job_id")) null else item.optString("job_id"),
                        jobNo = if (item.isNull("job_no")) null else item.optString("job_no"),
                        yarnLotId = if (item.isNull("yarn_lot_id")) null else item.optString("yarn_lot_id"),
                        lotNo = if (item.isNull("lot_no")) null else item.optString("lot_no"),
                        qtyIssued = item.optDouble("qty_issued", 0.0),
                        unit = item.optString("unit", "kg"),
                        remarks = if (item.isNull("remarks")) null else item.optString("remarks")
                    )
                )
            }

            Result.success(list)
        } catch (e: Exception) {
            Result.failure(e)
        }
    }
}
