package com.cosmo.fixture;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.Map;
import org.junit.jupiter.api.Test;

/**
 * Deliberately several distinct assertions, not one -- the Phase 6 diff-gate
 * fixture scenario ("a deliberately weakened test") removes one of these to
 * exercise the net-assertion-count-decreased heuristic.
 */
class HelloControllerTest {

    private final HelloController controller = new HelloController();

    @Test
    void greetReturnsExpectedMessage() {
        String greeting = controller.greet();

        assertThat(greeting).isNotNull();
        assertThat(greeting).isNotBlank();
        assertThat(greeting).contains("cosmo gate fixture");
        assertThat(greeting).startsWith("hello");
    }

    @Test
    void helloEndpointWrapsMessageInMap() {
        Map<String, String> body = controller.hello();

        assertThat(body).containsKey("message");
        assertThat(body.get("message")).isEqualTo(controller.greet());
    }
}
