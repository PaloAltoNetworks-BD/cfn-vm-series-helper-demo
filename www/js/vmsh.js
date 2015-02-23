(function(d, w) {
	$(d).ready(function() {
		function handleStackEvents() {
			$("div.events").hide();
			var hash = w.location.hash;
			if(hash) {
				$("#events-stack-"+hash.substr(1)).show();
				$("tr.reqselected").removeClass('reqselected');
				$("#request-"+hash.substr(1)).addClass('reqselected');
			}
		};

		$("body").on("click", "tr.request", function() {
			w.location.hash = $(this).data('stack');
			handleStackEvents();
		});
		$("body").on("click", "tr.event-header", function() {
			var $fspan = $(this).children("td:first").children("span:first");
			$fspan.toggleClass("glyphicon-chevron-right");
			$fspan.toggleClass("glyphicon-chevron-down");
	
			var $eventtr = $(this).next(".collapse");
			$eventtr.toggleClass("in");
			$eventtr.toggleClass("out");
		});
		handleStackEvents();
	});
})(document, window);
