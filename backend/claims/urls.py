from django.urls import path

from . import views

urlpatterns = [
    path("spike/stream/", views.spike_stream_proxy, name="spike-stream-proxy"),
    path("claims/", views.ClaimListCreateView.as_view(), name="claim-list-create"),
    path("claims/<str:claim_id>/", views.ClaimDetailView.as_view(), name="claim-detail"),
    path("claims/<str:claim_id>/resume/", views.ClaimResumeView.as_view(), name="claim-resume"),
]
