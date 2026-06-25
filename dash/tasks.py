# Task stubs kept for future scheduled jobs (email, chat, discounts).


# def send_email(text_msg: str, html_msg: str, subj: str, email: str) -> None:
#     url: str = f"http://{settings.SENDER_HOST}/send-mail"
#     data = {
#         "to": email,
#         "subject": subj,
#         "htmlcontent": html_msg,
#         "textcontent": text_msg,
#     }
#     response: requests.Response = requests.post(url, json=data)
#     if not response.status_code >= 200 and not response.status_code <= 299:
#         print("Error Sending Email")
#     return


# def send_to_rocket_chat(msg: str) -> None:
#     url = settings.ROCKET_CHAT_WEBHOOK
#     message = {
#         "emoji": ":ghost:",
#         "text": f"[CyberHotline Academy] {msg}",
#     }
#     response = requests.post(url, json=message)
#     if not response.ok:
#         print("Unable to send to rocket.chat webhook")


# def discount_expiration(id: str):
#     if Discount.objects.filter(id=id).exists():
#         expiration_date = Discount.objects.get(id=id).valid_to
#         if expiration_date == timezone.now():
#             dc = Discount.objects.get(id=id)
#             dc.enabled = False
#             dc.save()


# def voucher_expiration(id: str):
#     if Voucher.objects.filter(id=id).exists():
#         expire_date = Voucher.objects.get(id=id).expires_on
#         if expire_date == timezone.now():
#             vc = Voucher.objects.get(id=id)
#             vc.enabled = False
#             vc.save()

# def streak_cron():
#     get_user_model().objects.all()
#     cutoff = timezone.now().date() - timedelta(days=1)
#     StudentProfile.objects.filter(last_activity_date__lt=cutoff).update(
#         current_streak=0
#     )
